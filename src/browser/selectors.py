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
}
