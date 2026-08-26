from app.services.product_compatibility import check_product_compatibility, classify_product


def test_explicit_package_form_wins_over_negative_forbidden_terms() -> None:
    profile = (
        "产品名称：舒蕾发膜；产品类别：发膜；主包装：罐装\n"
        "必须锁定：不得生成泵头、喷头、软管、吸管或盒装开口"
    )

    assert classify_product(profile) == "jar"


def test_generic_box_is_not_misclassified_as_a_drink_carton() -> None:
    assert classify_product("产品类别：面霜；主包装：盒装") == "box"
    assert classify_product("纸盒装牛奶，顶部有吸管口") == "carton"


def test_jar_pump_action_is_repairable_with_a_jar_interaction() -> None:
    product_kind, conflicts = check_product_compatibility(
        "产品名称：舒蕾发膜；主包装：罐装",
        [{
            "shot_id": "shot-1", "start_sec": 1.0, "end_sec": 2.0,
            "facts": {"product_interaction": "一手握住瓶身，另一手按压泵头挤出内容物"},
        }],
    )

    assert product_kind == "jar"
    assert len(conflicts) == 1
    assert conflicts[0]["severity"] == "adaptable"
    assert "旋开罐盖" in conflicts[0]["suggestion"]


def test_negated_pump_or_spray_observation_is_not_an_action_conflict() -> None:
    _, conflicts = check_product_compatibility(
        "产品名称：舒蕾发膜；主包装：罐装",
        [
            {"shot_id": "a", "facts": {"product_interaction": "双手擦拭瓶身；未可靠确认泵头被按压或液体被挤出。"}},
            {"shot_id": "b", "facts": {"product_interaction": "产品静置，人物未直接拿取、喷洒或触碰产品。"}},
            {"shot_id": "c", "facts": {"product_interaction": "手部抓握并移动产品，未观察到按压泵头或挤出液体的动作。"}},
        ],
    )

    assert conflicts == []


def test_jar_sniff_action_preserves_the_story_function() -> None:
    _, conflicts = check_product_compatibility(
        "产品名称：舒蕾发膜；主包装：罐装",
        [{"shot_id": "sniff", "facts": {"product_interaction": "将按压泵头靠近鼻部嗅闻香气"}}],
    )

    assert len(conflicts) == 1
    assert "开口靠近鼻部轻嗅" in conflicts[0]["suggestion"]
    assert "保留嗅闻香气的叙事功能" in conflicts[0]["suggestion"]
