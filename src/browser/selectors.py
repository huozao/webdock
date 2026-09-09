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
#
# Dumped 2026-08-17 (project page and conversation page are identical): THREE
# file inputs live under the composer — the first sits in a `div.hidden` inside
# `form.group/composer` and takes anything (accept=""), the other two are
# accept="image/*". The generic one is the right target because images and
# documents share this path, so scope to the composer's form first and keep the
# bare selector only as the fallback: it is what a stray file input elsewhere on
# the page (project files, settings dialogs) would otherwise match first.
FILE_INPUT = [
    "form[class*='composer'] input[type='file']",
    "[class*='composer'] input[type='file']",
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
    "[data-testid^='conversation-turn-']:not([data-testid='conversation-turn-location-footer'])",
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
# 无文案约束的胶囊本体。ensure_mode 先用它问一次"现在写着什么"，带文案的候选
# 只在它落空时兜底——逐个候选各等满一轮超时曾是发送前最大的一段固定开销
# （2026-07-28 实测 6.0s/条，且多数时候只是确认模式已经对了）。
MODE_PICKER_BUTTON_ANY = "button[aria-haspopup='menu'][class*='__composer-pill']"
MODE_PICKER_BUTTON = [
    "button[aria-haspopup='menu'][class*='__composer-pill']:has-text('极速')",
    "button[aria-haspopup='menu'][class*='__composer-pill']:has-text('均衡')",
    "button[aria-haspopup='menu'][class*='__composer-pill']:has-text('高级')",
    MODE_PICKER_BUTTON_ANY,
]

# 真机菜单项是 menuitemradio(极速/均衡/高级, aria-checked 标当前)；GPT-5.5
# 子菜单是 menuitem，被更靠前的 menuitemradio 命中规则天然避开。
MODE_MENU_ITEM = [
    "[role='menuitemradio']",
    "[role='menuitem']",
    "[role='option']",
]


# ChatGPT's own "generation failed" banner ("Something went wrong while
# generating the response…", with a Retry button). Nothing else on the page
# reports this — the turn simply never completes — so without matching it the
# request burns the full timeout before failing with a misleading RESPONSE_TIMEOUT.
GENERATION_ERROR_BANNER = [
    "[data-testid='regenerate-thread-error-button']",
    "div[class*='text-token-text-error']",
    "div[role='alert']",
]
# Only text matching this is treated as the banner — the selectors above can also
# match unrelated inline warnings.
GENERATION_ERROR_TEXTS = (
    "something went wrong while generating",
    "生成回复时出错",
    "生成响应时出现问题",
)


# Left-rail project rows, used to reach a project home page through in-app
# routing instead of a full navigation (see manager.open_project_home).
#
# Dumped 2026-09-09 on both devices. Three things that look like handles are NOT
# available here: the row carries no gizmo id anywhere in its subtree, it is not
# an <a href>, and clicking the row's own label does not navigate at all. The
# only working entry point is the row's trailing "open project home" button.
SIDEBAR_PROJECT_ITEM = [
    "div[data-sidebar-item][role='button'].__menu-item",
    "div[data-sidebar-item][role='button']",
]

# The row has TWO trailing buttons and they must not be confused: the second one
# opens the options menu and never navigates. Tell them apart by structure, not
# by position or label — only the menu one carries aria-haspopup, while the
# aria-label is localized ("Open project home" on webdock2's en-US UI,
# "打开项目首页" on webdock1's zh-CN UI) and would silently miss on the standby.
SIDEBAR_PROJECT_HOME_BUTTON = [
    "button[data-trailing-button]:not([aria-haspopup])",
]

# Collapsed-projects expander. Projects past the first few are hidden behind it,
# so a row lookup that misses must expand before concluding the project is gone
# (2026-09-09: weixin-b, a routed production project, sits below the fold).
# It is a <button data-sidebar-item>, whereas project rows are <div role=button>,
# so the structural selector above never matches it.
SIDEBAR_SHOW_MORE_TEXTS = ("show more", "查看更多")

SELECTOR_GROUPS = {
    "CHAT_INPUT": CHAT_INPUT,
    "GENERATION_ERROR_BANNER": GENERATION_ERROR_BANNER,
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
    "SIDEBAR_PROJECT_ITEM": SIDEBAR_PROJECT_ITEM,
    "SIDEBAR_PROJECT_HOME_BUTTON": SIDEBAR_PROJECT_HOME_BUTTON,
}
