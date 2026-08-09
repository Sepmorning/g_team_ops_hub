from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Iterable


@dataclass(frozen=True)
class CountryDefinition:
    code: str
    name: str
    aliases: tuple[str, ...] = ()


COUNTRY_DEFINITIONS = (
    CountryDefinition("US", "美国", ("美站",)),
    CountryDefinition("CA", "加拿大", ("加站",)),
    CountryDefinition("UK", "英国", ("英国站", "GB")),
    CountryDefinition("DE", "德国", ("德站",)),
    CountryDefinition("FR", "法国", ("法站",)),
    CountryDefinition("IT", "意大利", ("意站",)),
    CountryDefinition("ES", "西班牙", ("西站",)),
    CountryDefinition("JP", "日本", ("日站",)),
    CountryDefinition("AU", "澳大利亚", ("澳洲", "澳大利亚站")),
    CountryDefinition("MX", "墨西哥", ("墨西哥站",)),
    CountryDefinition("BR", "巴西", ("巴西站",)),
    CountryDefinition("NL", "荷兰", ("荷兰站",)),
    CountryDefinition("SE", "瑞典", ("瑞典站",)),
    CountryDefinition("PL", "波兰", ("波兰站",)),
    CountryDefinition("BE", "比利时", ("比利时站",)),
)


@dataclass
class DiscoveredSite:
    country_code: str
    country_name: str
    listing_sheet_name: str = ""
    fba_sheet_name: str = ""
    detail_sheet_name: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return bool(
            self.listing_sheet_name
            and self.fba_sheet_name
            and self.detail_sheet_name
        )


def normalize_sheet_name(value: str) -> str:
    return (
        str(value or "")
        .strip()
        .replace("－", "-")
        .replace("—", "-")
        .replace("–", "-")
        .replace("﹣", "-")
        .replace(" ", "")
        .upper()
    )


def normalize_country_code(value: str) -> str:
    code = str(value or "").strip().upper()
    if code == "GB":
        return "UK"
    return code


def country_definition(
    *, code: str = "", name: str = ""
) -> CountryDefinition | None:
    normalized_code = normalize_country_code(code)
    normalized_name = normalize_sheet_name(name)
    for item in COUNTRY_DEFINITIONS:
        if normalized_code and item.code == normalized_code:
            return item
        names = (item.name, *item.aliases)
        if normalized_name and any(
            normalize_sheet_name(candidate) == normalized_name for candidate in names
        ):
            return item
    return None


def infer_country_code(country_name: str) -> str:
    item = country_definition(name=country_name)
    return item.code if item else ""


def default_site_sheet_names(
    shop_name: str, country_name: str, country_code: str
) -> tuple[str, str, str]:
    code = normalize_country_code(country_code)
    listing = f"{shop_name.strip()}-{country_name.strip()}" if shop_name.strip() else ""
    return (
        listing,
        f"{code}-FBA" if code else "",
        f"{code}-轨迹明细" if code else "",
    )


def listing_prefixes(sheet_names: Iterable[str]) -> list[str]:
    """返回工作簿中按“前缀-国家”识别到的唯一Listing前缀。"""
    prefixes: dict[str, str] = {}
    for raw in sheet_names:
        name = (
            str(raw or "")
            .strip()
            .replace("－", "-")
            .replace("—", "-")
            .replace("–", "-")
            .replace("﹣", "-")
        )
        normalized_name = normalize_sheet_name(name)
        for definition in COUNTRY_DEFINITIONS:
            labels = (
                definition.name,
                *(
                    alias
                    for alias in definition.aliases
                    if not re.fullmatch(r"[A-Z]{2,3}", alias.upper())
                ),
            )
            if any(
                normalized_name.endswith("-" + normalize_sheet_name(label))
                for label in labels
            ):
                display_prefix = name.rsplit("-", 1)[0].strip()
                normalized_prefix = normalize_sheet_name(display_prefix)
                if display_prefix and normalized_prefix:
                    prefixes.setdefault(normalized_prefix, display_prefix)
                break
    return [prefixes[key] for key in sorted(prefixes)]


def discover_sites(
    shop_name: str,
    sheet_names: Iterable[str],
    *,
    listing_prefix: str | None = None,
) -> list[DiscoveredSite]:
    """按明确命名规则识别站点；不对歧义名称做危险的模糊匹配。"""
    exact: dict[str, list[str]] = {}
    for raw in sheet_names:
        name = str(raw or "").strip()
        if not name:
            continue
        exact.setdefault(normalize_sheet_name(name), []).append(name)

    normalized_shop = normalize_sheet_name(shop_name)
    listing_candidates_by_country: dict[str, list[tuple[str, str]]] = {
        item.code: [] for item in COUNTRY_DEFINITIONS
    }
    detected_prefixes: set[str] = set()
    for names in exact.values():
        for name in names:
            normalized_name = normalize_sheet_name(name)
            for definition in COUNTRY_DEFINITIONS:
                labels = (
                    definition.name,
                    *(
                        alias
                        for alias in definition.aliases
                        if not re.fullmatch(r"[A-Z]{2,3}", alias.upper())
                    ),
                )
                matched = False
                for country_label in labels:
                    suffix = "-" + normalize_sheet_name(country_label)
                    if (
                        normalized_name.endswith(suffix)
                        and len(normalized_name) > len(suffix)
                    ):
                        prefix = normalized_name[: -len(suffix)]
                        listing_candidates_by_country[definition.code].append(
                            (prefix, name)
                        )
                        detected_prefixes.add(prefix)
                        matched = True
                        break
                if matched:
                    break

    if listing_prefix is not None:
        selected_listing_prefix = normalize_sheet_name(listing_prefix)
    elif normalized_shop in detected_prefixes:
        selected_listing_prefix = normalized_shop
    elif len(detected_prefixes) == 1:
        selected_listing_prefix = next(iter(detected_prefixes))
    else:
        selected_listing_prefix = ""
    prefix_warning = ""
    if (
        listing_prefix is None
        and selected_listing_prefix
        and selected_listing_prefix != normalized_shop
    ):
        prefix_warning = (
            f"店铺名称“{shop_name.strip()}”与Listing子表前缀不一致，"
            "已按工作簿中唯一稳定的Listing前缀识别"
        )

    discovered: list[DiscoveredSite] = []
    for definition in COUNTRY_DEFINITIONS:
        site = DiscoveredSite(definition.code, definition.name)
        listing_candidates = [
            name
            for prefix, name in listing_candidates_by_country[definition.code]
            if selected_listing_prefix and prefix == selected_listing_prefix
        ]
        listing_candidates = list(dict.fromkeys(listing_candidates))
        fba_candidates = exact.get(normalize_sheet_name(f"{definition.code}-FBA"), [])
        detail_candidates = exact.get(
            normalize_sheet_name(f"{definition.code}-轨迹明细"), []
        )

        if len(listing_candidates) == 1:
            site.listing_sheet_name = listing_candidates[0]
        elif len(listing_candidates) > 1:
            site.warnings.append("发现多个Listing候选子表，需要人工确认")
        elif (
            listing_candidates_by_country[definition.code]
            and not selected_listing_prefix
        ):
            site.warnings.append(
                "工作簿中存在多个Listing店铺前缀，无法安全判断目标子表"
            )
        if len(fba_candidates) == 1:
            site.fba_sheet_name = fba_candidates[0]
        elif len(fba_candidates) > 1:
            site.warnings.append("发现多个FBA主表，需要人工确认")
        if len(detail_candidates) == 1:
            site.detail_sheet_name = detail_candidates[0]
        elif len(detail_candidates) > 1:
            site.warnings.append("发现多个轨迹明细表，需要人工确认")

        typo_candidates = exact.get(
            normalize_sheet_name(f"{definition.code}-FAB"), []
        )
        if typo_candidates and not site.fba_sheet_name:
            site.warnings.append(
                f"发现疑似拼写错误的子表“{typo_candidates[0]}”，请确认是否应改为{definition.code}-FBA"
            )

        if prefix_warning and site.listing_sheet_name:
            site.warnings.append(prefix_warning)

        if (
            site.listing_sheet_name
            or site.fba_sheet_name
            or site.detail_sheet_name
            or site.warnings
        ):
            discovered.append(site)

    # 对“代码-FBA”这种暂未收录的站点给出可编辑候选，不擅自推断国家中文名。
    known_codes = {item.code for item in COUNTRY_DEFINITIONS}
    for normalized, names in exact.items():
        match = re.fullmatch(r"([A-Z]{2,3})-FBA", normalized)
        if not match or match.group(1) in known_codes or len(names) != 1:
            continue
        code = match.group(1)
        detail = exact.get(normalize_sheet_name(f"{code}-轨迹明细"), [])
        discovered.append(
            DiscoveredSite(
                country_code=code,
                country_name=code,
                fba_sheet_name=names[0],
                detail_sheet_name=detail[0] if len(detail) == 1 else "",
                warnings=["国家代码不在内置列表中，请人工补充国家名称和Listing子表"],
            )
        )
    return discovered
