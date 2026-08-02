from anda_tracker.sites import (
    discover_sites,
    listing_prefixes,
    normalize_country_code,
)


def test_discovers_standard_listing_fba_and_detail_sheet_groups():
    sites = discover_sites(
        "纯粹",
        [
            "说明",
            "纯粹-美国",
            "US-FBA",
            "US-轨迹明细",
            "纯粹-加拿大",
            "CA-FBA",
            "CA-轨迹明细",
        ],
    )

    assert [
        (
            item.country_code,
            item.country_name,
            item.listing_sheet_name,
            item.fba_sheet_name,
            item.detail_sheet_name,
        )
        for item in sites
    ] == [
        ("US", "美国", "纯粹-美国", "US-FBA", "US-轨迹明细"),
        ("CA", "加拿大", "纯粹-加拿大", "CA-FBA", "CA-轨迹明细"),
    ]


def test_fab_typo_is_warned_but_never_bound_as_fba_sheet():
    sites = discover_sites("纯粹", ["纯粹-英国", "UK-FAB"])

    assert len(sites) == 1
    assert sites[0].country_code == "UK"
    assert sites[0].listing_sheet_name == "纯粹-英国"
    assert sites[0].fba_sheet_name == ""
    assert "疑似拼写错误" in sites[0].warnings[0]
    assert normalize_country_code("gb") == "UK"


def test_unique_listing_prefix_is_used_when_display_shop_name_differs():
    sites = discover_sites(
        "纯粹测试",
        [
            "纯粹-美国",
            "US-FBA",
            "US-轨迹明细",
            "纯粹-加拿大",
            "CA-FBA",
            "CA-轨迹明细",
            "纯粹-英国",
            "UK-FBA",
        ],
    )

    assert [item.country_code for item in sites] == ["US", "CA", "UK"]
    assert sites[1].listing_sheet_name == "纯粹-加拿大"
    assert sites[1].fba_sheet_name == "CA-FBA"
    assert sites[1].detail_sheet_name == "CA-轨迹明细"
    assert any("唯一稳定的Listing前缀" in value for value in sites[1].warnings)
    assert listing_prefixes(
        ["纯粹-美国", "纯粹-加拿大", "WpsReserved_CellImgList"]
    ) == ["纯粹"]

    strict_sites = discover_sites(
        "纯粹测试",
        ["纯粹-美国", "US-FBA", "纯粹-加拿大", "CA-FBA"],
        listing_prefix="纯粹",
    )
    assert [item.listing_sheet_name for item in strict_sites] == [
        "纯粹-美国",
        "纯粹-加拿大",
    ]
    assert not any(item.warnings for item in strict_sites)


def test_multiple_unrelated_listing_prefixes_are_not_auto_bound():
    sites = discover_sites(
        "测试店铺",
        ["纯粹-美国", "其他-加拿大", "US-FBA", "CA-FBA"],
    )

    assert [item.listing_sheet_name for item in sites] == ["", ""]
    assert all(
        any("多个Listing店铺前缀" in warning for warning in item.warnings)
        for item in sites
    )
