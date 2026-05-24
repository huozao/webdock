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

ASSISTANT_MESSAGE = [
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

STOP_BUTTON = [
    "button[data-testid='stop-button']",
    "button[aria-label*='Stop']",
    "button[aria-label*='停止']",
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
    "ASSISTANT_MESSAGE": ASSISTANT_MESSAGE,
    "COPY_BUTTON": COPY_BUTTON,
    "STOP_BUTTON": STOP_BUTTON,
    "LOGIN_INDICATORS": LOGIN_INDICATORS,
}
