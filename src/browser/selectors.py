from __future__ import annotations


CHAT_INPUT = [
    "#prompt-textarea",
    "div[contenteditable='true'][id='prompt-textarea']",
    "textarea[data-testid='prompt-textarea']",
    "div[contenteditable='true']",
]

SEND_BUTTON = [
    "button[data-testid='send-button']",
    "button[aria-label*='Send']",
    "button[aria-label*='发送']",
]

# ChatGPT's composer hides a multi-file <input type="file"> that the "attach"
# button drives; we set files on it directly (no visible click needed) to attach
# an inbound WeChat image. Matched in the "attached" (not visible) DOM state.
FILE_INPUT = [
    "input[type='file']",
]

# The composer's preview of a just-attached file (image thumbnail / remove chip).
# Used as a best-effort "upload finished" signal before sending; selectors may
# drift, so callers must tolerate none of these matching.
ATTACHMENT_PREVIEW = [
    "[data-testid$='-attachment']",
    "img[alt='Uploaded image']",
    "div[class*='attachment'] img",
    "button[aria-label*='Remove']",
    "button[aria-label*='移除']",
]

ASSISTANT_MESSAGE = [
    # Current ChatGPT DOM: every message is a conversation-turn (user + assistant);
    # image/reasoning replies no longer carry data-message-author-role nor a
    # .markdown body, so the legacy author-role/article selectors miss them. The
    # author-role/agent-turn entries stay as fallbacks for older/text replies.
    "[data-testid^='conversation-turn']",
    "article:has([data-message-author-role='assistant'])",
    "div[data-message-author-role='assistant']",
    "[data-message-author-role='assistant']",
    "article:has(.agent-turn)",
    "div.agent-turn",
]

COPY_BUTTON = [
    "button[data-testid='copy-turn-action-button']",
    "button[aria-label='Copy']",
    "button[aria-label*='复制']",
]

# Only the real "stop generating" button. The previous broad aria-label*='Stop'
# / '停止' matched unrelated buttons that appear AFTER generation finishes (e.g.
# "停止朗读" / read-aloud), which made the completion check hang until timeout.
STOP_BUTTON = [
    "button[data-testid='stop-button']",
]

# Present only while a reply is actively streaming. Primary "still generating"
# signal (more reliable than the stop button).
STREAMING_INDICATOR = [
    "[data-message-author-role='assistant'] .result-streaming",
    ".result-streaming",
    "[data-message-author-role='assistant'].result-streaming",
]

LOGIN_INDICATORS = [
    "button[data-testid='login-button']",
    "button:has-text('Log in')",
    "button:has-text('登录')",
    "a[href*='auth.openai.com']",
]

# ChatGPT 输入区的对话模式选择器（当前中文界面：极速/均衡/高级）。按钮文本
# 显示当前模式；菜单项在运行时按文本匹配（见 chatgpt_page.ensure_mode）。
# 候选列表按真机 DOM 校准后排序；ensure_mode 对全部不命中容错（模式切换是
# best-effort，绝不阻断发送）。
# 真机(2026-07-06, Chrome 149/中文界面)：composer 右下的"模式胶囊"按钮，无
# data-testid/aria-label，靠 __composer-pill 类 + aria-haspopup + 当前模式文本
# 锚定；按钮文本 = 当前模式。前三个候选同时确认了"这就是模式胶囊"；最后一个
# 是 UI 文案变动时的宽松兜底。
MODE_PICKER_BUTTON = [
    "button[aria-haspopup='menu'][class*='__composer-pill']:has-text('极速')",
    "button[aria-haspopup='menu'][class*='__composer-pill']:has-text('均衡')",
    "button[aria-haspopup='menu'][class*='__composer-pill']:has-text('高级')",
    "button[aria-haspopup='menu'][class*='__composer-pill']",
]

# 真机菜单项是 menuitemradio(极速/均衡/高级, aria-checked 标当前)；GPT-5.5
# 子菜单是 menuitem，被更靠前的 menuitemradio 命中规则天然避开。
MODE_MENU_ITEM = [
    "[role='menuitemradio']",
    "[role='menuitem']",
    "[role='option']",
]


SELECTOR_GROUPS = {
    "CHAT_INPUT": CHAT_INPUT,
    "SEND_BUTTON": SEND_BUTTON,
    "FILE_INPUT": FILE_INPUT,
    "ATTACHMENT_PREVIEW": ATTACHMENT_PREVIEW,
    "ASSISTANT_MESSAGE": ASSISTANT_MESSAGE,
    "COPY_BUTTON": COPY_BUTTON,
    "STOP_BUTTON": STOP_BUTTON,
    "STREAMING_INDICATOR": STREAMING_INDICATOR,
    "LOGIN_INDICATORS": LOGIN_INDICATORS,
    "MODE_PICKER_BUTTON": MODE_PICKER_BUTTON,
    "MODE_MENU_ITEM": MODE_MENU_ITEM,
}
