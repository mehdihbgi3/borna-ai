from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal
from typing import Iterable, Mapping

ONE = Decimal(1)


@dataclass(frozen=True)
class PricingParams:
    base_rate_rial: int
    alpha_default: Decimal
    round_step_rial: int
    publish_threshold: Decimal
    approval_threshold: Decimal
    change_basis: str = "exact"

    @classmethod
    def from_config(cls, pcfg) -> "PricingParams":
        return cls(base_rate_rial=pcfg.base_rate_rial, alpha_default=pcfg.alpha_default,
                   round_step_rial=pcfg.round_step_rial, publish_threshold=pcfg.publish_threshold,
                   approval_threshold=pcfg.approval_threshold, change_basis=pcfg.change_basis)


@dataclass(frozen=True)
class ProductInput:
    key: str
    base_price_rial: int
    alpha: Decimal | None = None


@dataclass(frozen=True)
class RefList:
    rate_rial: int
    prices: Mapping[str, int]


@dataclass
class PriceRow:
    key: str
    base_price_rial: int
    alpha: Decimal
    exact_rial: Decimal
    price_rial: int
    change_vs_base: Decimal
    pub_price_rial: int | None
    change_vs_pub: Decimal | None
    fx_effect_vs_pub: Decimal | None
    day_price_rial: int | None
    change_vs_day: Decimal | None
    fx_effect_vs_day: Decimal | None
    redefined_vs_pub: bool
    flag_publish: bool
    flag_approval: bool


@dataclass
class RunResult:
    rate_rial: int
    rows: list[PriceRow]
    removed_keys: list[str] = field(default_factory=list)
    any_price_diff: bool = False
    needs_publish: bool = False
    needs_approval: bool = False

    @property
    def n_publish(self) -> int:
        return sum(r.flag_publish for r in self.rows)

    @property
    def n_approval(self) -> int:
        return sum(r.flag_approval for r in self.rows)


def exact_price(p0: int | Decimal, alpha: Decimal, rate_rial: int | Decimal, base_rate_rial: int | Decimal) -> Decimal:
    p0, rate, base = Decimal(p0), Decimal(rate_rial), Decimal(base_rate_rial)
    return p0 * (alpha * rate / base + (ONE - alpha))


def round_price(x: Decimal, step: int) -> int:
    st = Decimal(step)
    return int((x / st).quantize(ONE, rounding=ROUND_HALF_UP) * st)


def rel(new, old) -> Decimal | None:
    if old in (None, 0):
        return None
    return Decimal(new) / Decimal(old) - ONE


def compute_run(products: Iterable[ProductInput], rate_rial: int, params: PricingParams,
                pub: RefList | None, day: RefList | None) -> RunResult:
    rows: list[PriceRow] = []
    s0, step = params.base_rate_rial, params.round_step_rial
    thr_pub, thr_day = params.publish_threshold, params.approval_threshold
    exact_basis = params.change_basis == "exact"
    keys = []
    for p in products:
        keys.append(p.key)
        alpha = params.alpha_default if p.alpha is None else Decimal(p.alpha)
        ex = exact_price(p.base_price_rial, alpha, rate_rial, s0)
        pr = round_price(ex, step)

        pub_price = pub.prices.get(p.key) if pub else None
        day_price = day.prices.get(p.key) if day else None

        fx_pub = fx_day = None
        redefined_pub = pub_price is None
        redefined_day = day_price is None
        if pub_price is not None:
            ex_at_pub = exact_price(p.base_price_rial, alpha, pub.rate_rial, s0)
            fx_pub = rel(ex, ex_at_pub)
            redefined_pub = round_price(ex_at_pub, step) != pub_price
        if day_price is not None:
            ex_at_day = exact_price(p.base_price_rial, alpha, day.rate_rial, s0)
            fx_day = rel(ex, ex_at_day)
            redefined_day = round_price(ex_at_day, step) != day_price

        chg_pub = rel(pr, pub_price)
        chg_day = rel(pr, day_price)

        if exact_basis:
            flag_pub = redefined_pub or (fx_pub is not None and abs(fx_pub) > thr_pub)
            flag_appr = (fx_day is not None and abs(fx_day) > thr_day) or (
                redefined_day and chg_day is not None and abs(chg_day) > thr_day)
        else:
            flag_pub = pub_price is None or abs(chg_pub) > thr_pub
            flag_appr = chg_day is not None and abs(chg_day) > thr_day

        rows.append(PriceRow(
            key=p.key, base_price_rial=int(p.base_price_rial), alpha=alpha, exact_rial=ex, price_rial=pr,
            change_vs_base=rel(pr, p.base_price_rial) or Decimal(0),
            pub_price_rial=pub_price, change_vs_pub=chg_pub, fx_effect_vs_pub=fx_pub,
            day_price_rial=day_price, change_vs_day=chg_day, fx_effect_vs_day=fx_day,
            redefined_vs_pub=redefined_pub, flag_publish=bool(flag_pub), flag_approval=bool(flag_appr),
        ))

    res = RunResult(rate_rial=int(rate_rial), rows=rows)
    if pub is None:
        res.any_price_diff = True
    else:
        res.removed_keys = sorted(set(pub.prices) - set(keys))
        res.any_price_diff = bool(res.removed_keys) or any(r.price_rial != r.pub_price_rial for r in rows)
    res.needs_publish = res.any_price_diff and (bool(res.removed_keys) or any(r.flag_publish for r in rows))
    res.needs_approval = res.needs_publish and any(r.flag_approval for r in rows)
    return res
