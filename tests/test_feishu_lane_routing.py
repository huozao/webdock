from __future__ import annotations

import json

from src.browser.lane_routing import LaneRouter
from src.browser.lane_scheduler import LaneContext


def _write_config(path, lanes):
    path.write_text(json.dumps({"lanes": lanes}, ensure_ascii=False), encoding="utf-8")


def test_feishu_lane_key_isolated_from_wechat_peer():
    feishu = LaneContext.from_metadata({"channel": "feishu", "peer_id": "ou_abc"})
    wechat = LaneContext.from_metadata({"wechat_account": "A", "peer_id": "ou_abc"})

    assert feishu.key == "feishu:ou_abc"
    assert wechat.key == "wechat:A:private:ou_abc"


def test_feishu_router_uses_feishu_projects_config(tmp_path):
    wechat_config = tmp_path / "wechat_projects.json"
    feishu_config = tmp_path / "feishu_projects.json"
    state = tmp_path / "lane_state.json"
    _write_config(wechat_config, {"ou_abc": {"name": "微信同名", "project_url": "https://chatgpt.com/g/w/project"}})
    _write_config(feishu_config, {"ou_abc": {"name": "飞书同名", "project_url": "https://chatgpt.com/g/f/project"}})

    router = LaneRouter(config_path=wechat_config, state_path=state, feishu_config_path=feishu_config)

    assert router.resolve_target_url("ou_abc", channel="feishu") == "https://chatgpt.com/g/f/project"
    assert router.resolve_target_url("ou_abc", channel="wechat") == "https://chatgpt.com/g/w/project"


def test_wecom_lane_isolated_and_dynamic_conversation_is_persisted(tmp_path):
    wechat_config = tmp_path / "wechat_projects.json"
    feishu_config = tmp_path / "feishu_projects.json"
    wecom_config = tmp_path / "wecom_projects.json"
    state = tmp_path / "lane_state.json"
    _write_config(wechat_config, {})
    _write_config(feishu_config, {})
    _write_config(wecom_config, {})

    wecom = LaneContext.from_metadata(
        {"channel": "wecom", "account": "company-b", "chat_type": "group", "peer_id": "wr_group"}
    )
    wechat = LaneContext.from_metadata(
        {"wechat_account": "company-b", "chat_type": "group", "peer_id": "wr_group"}
    )
    assert wecom.key == "wecom:company-b:group:wr_group"
    assert wechat.key == "wechat:company-b:group:wr_group"

    router = LaneRouter(
        config_path=wechat_config,
        state_path=state,
        feishu_config_path=feishu_config,
        wecom_config_path=wecom_config,
    )
    assert router.is_configured("wr_group", channel="wecom") is True
    assert router.resolve_target_url("wr_group", channel="wecom") is None

    conversation = "https://chatgpt.com/c/wecom-conversation"
    router.record_conversation_url("wr_group", conversation, channel="wecom")
    assert router.resolve_target_url("wr_group", channel="wecom") == conversation
    assert json.loads(state.read_text(encoding="utf-8"))["wecom:wr_group"]["conversation_url"] == conversation
