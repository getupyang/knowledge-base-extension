// 版本化安装指令的 pin（清单 1.4 协议）：发布脚本随打 tag 一并更新，不追 main。
// ⚠ 当前为占位值：tag 尚未推送（清单 1.1 执行时打真实 tag + 推 GitHub Release 后生效）。
const MARGIN_RELEASE_PIN = "v0.4.0";

// popup 期望的后端 API schema（对齐 backend/agent_api.py 的 API_SCHEMA）。
// 后端返回值不等于它（包括旧后端没有此字段 = 0）时，E 态出"引擎需要升级"提示。
const MARGIN_EXPECTED_API_SCHEMA = 1;
