import json

from backend.app.providers.openai_compat import to_openai_messages


def test_tool_exchange_converted_to_openai_format():
    msgs = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"},
            {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_target_course", "arguments": {"course_id": "CS101"}}},
                                                                {"function": {"name": "check_rules", "arguments": {"target_course_id": "CS101"}}}]},
            {"role": "tool", "tool_name": "check_rules", "content": "{}"},
            {"role": "tool", "tool_name": "get_target_course", "content": "{}"}]
    out = to_openai_messages(msgs)
    calls = out[2]["tool_calls"]
    assert [c["id"] for c in calls] == ["call_1", "call_2"] and isinstance(calls[0]["function"]["arguments"], str)
    assert json.loads(calls[0]["function"]["arguments"]) == {"course_id": "CS101"}
    assert out[3]["tool_call_id"] == "call_2" and out[4]["tool_call_id"] == "call_1"  # matched by tool name, not order
