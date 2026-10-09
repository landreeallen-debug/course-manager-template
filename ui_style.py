"""Shared visual styling for the Streamlit app."""
from __future__ import annotations

import streamlit as st


def apply_blue_theme() -> None:
    """Inject a cohesive blue UI skin across Streamlit pages."""
    st.markdown(
        """
<style>
@import url('https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,650;9..144,760&family=Nunito:wght@400;600;700;800&display=swap');

:root {
  --cm-ink: #16324f;
  --cm-muted: #6f8298;
  --cm-navy: #0d335d;
  --cm-blue-900: #12436f;
  --cm-blue-700: #236fa7;
  --cm-blue-600: #2f83c8;
  --cm-blue-400: #7fc2f3;
  --cm-blue-100: #dcefff;
  --cm-blue-050: #f4faff;
  --cm-mint: #c8f4eb;
  --cm-lilac: #dfe8ff;
  --cm-paper: rgba(255, 255, 255, 0.9);
  --cm-paper-strong: rgba(255, 255, 255, 0.98);
  --cm-line: rgba(35, 111, 167, 0.18);
  --cm-shadow: 0 14px 34px rgba(22, 50, 79, 0.11);
}

html, body, [class*="css"] {
  font-family: "Nunito", ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
}

.stApp {
  color: var(--cm-ink);
  background:
    linear-gradient(180deg, rgba(231, 244, 255, 0.92) 0%, rgba(249, 252, 255, 0.98) 42%, rgba(241, 248, 255, 1) 100%),
    repeating-linear-gradient(135deg, rgba(35, 111, 167, 0.03) 0 1px, transparent 1px 18px);
}

.main .block-container,
[data-testid="stMainBlockContainer"] {
  max-width: 1260px;
  padding-top: 1.7rem;
  padding-bottom: 4rem;
}

[data-testid="stSidebar"] {
  background:
    linear-gradient(180deg, #f7fbff 0%, #edf7ff 100%);
  border-right: 1px solid var(--cm-line);
  box-shadow: 12px 0 30px rgba(22, 50, 79, 0.08);
}

[data-testid="stSidebar"] * {
  color: var(--cm-ink);
}

[data-testid="stSidebar"] a {
  border-radius: 8px;
  margin: 0.12rem 0.35rem;
  font-weight: 800;
}

[data-testid="stSidebar"] a:hover {
  background: rgba(127, 194, 243, 0.18);
}

[data-testid="stSidebar"] a[aria-current="page"],
[data-testid="stSidebar"] a[aria-selected="true"] {
  background: #dcefff;
  border: 1px solid rgba(35, 111, 167, 0.18);
}

h1 {
  color: var(--cm-navy);
  font-family: "Fraunces", Georgia, serif;
  font-size: 3.25rem;
  font-weight: 760;
  letter-spacing: 0;
  line-height: 1.02;
  padding: 0.1rem 0 0.35rem;
  border-bottom: 1px solid var(--cm-line);
}

h2, h3 {
  color: var(--cm-blue-900);
  font-family: "Nunito", ui-sans-serif, system-ui, sans-serif;
  font-weight: 800;
  letter-spacing: 0;
}

p, li, label, [data-testid="stMarkdownContainer"] {
  color: var(--cm-ink);
}

[data-testid="stCaptionContainer"],
.stCaption,
small {
  color: var(--cm-muted) !important;
}

div[data-testid="stVerticalBlockBorderWrapper"],
[data-testid="stExpander"],
div[data-testid="stForm"],
[data-testid="stChatMessage"],
div[data-testid="stContainer"] > div[data-testid="stVerticalBlock"] {
  border-radius: 8px;
}

div[data-testid="stVerticalBlockBorderWrapper"] {
  background: var(--cm-paper);
  border: 1px solid var(--cm-line);
  box-shadow: var(--cm-shadow);
}

[data-testid="stExpander"] {
  background: var(--cm-paper);
  border: 1px solid var(--cm-line);
  box-shadow: 0 10px 24px rgba(22, 50, 79, 0.07);
  overflow: hidden;
}

[data-testid="stExpander"] summary {
  color: var(--cm-blue-900);
  font-weight: 800;
}

div[data-testid="stForm"] {
  background: var(--cm-paper-strong);
  border: 1px solid var(--cm-line);
  padding: 1rem;
  box-shadow: 0 12px 26px rgba(22, 50, 79, 0.08);
}

[data-testid="stAlert"] {
  border-radius: 8px;
  border: 1px solid rgba(47, 131, 200, 0.2);
  box-shadow: 0 10px 24px rgba(22, 50, 79, 0.07);
}

.stTabs [data-baseweb="tab-list"] {
  gap: 0.35rem;
  padding: 0.24rem;
  background: rgba(255, 255, 255, 0.72);
  border: 1px solid var(--cm-line);
  border-radius: 8px;
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
  flex-wrap: nowrap;
}

.stTabs [data-baseweb="tab"] {
  height: 2.45rem;
  padding: 0.35rem 0.85rem;
  border-radius: 7px;
  color: var(--cm-blue-900);
  font-weight: 800;
}

.stTabs [aria-selected="true"] {
  color: #ffffff !important;
  background: linear-gradient(135deg, var(--cm-blue-700), var(--cm-blue-400));
  box-shadow: 0 8px 18px rgba(35, 111, 167, 0.22);
}

div.stButton > button,
div.stDownloadButton > button,
[data-testid="stFormSubmitButton"] button {
  min-height: 2.5rem;
  border-radius: 8px;
  border: 1px solid rgba(35, 111, 167, 0.26);
  color: var(--cm-blue-900);
  background: rgba(255, 255, 255, 0.9);
  box-shadow: 0 7px 16px rgba(22, 50, 79, 0.08);
  font-weight: 800;
}

div.stButton > button:hover,
div.stDownloadButton > button:hover,
[data-testid="stFormSubmitButton"] button:hover {
  color: #ffffff;
  border-color: rgba(35, 111, 167, 0.7);
  background: linear-gradient(135deg, var(--cm-blue-700), var(--cm-blue-400));
  box-shadow: 0 12px 24px rgba(35, 111, 167, 0.22);
}

div.stButton > button[kind="primary"],
[data-testid="stFormSubmitButton"] button[kind="primary"] {
  color: #ffffff;
  border-color: rgba(127, 194, 243, 0.38);
  background: linear-gradient(135deg, var(--cm-blue-900), var(--cm-blue-600));
}

[data-testid="stTextInput"] input,
[data-testid="stTextArea"] textarea,
[data-testid="stNumberInput"] input,
[data-testid="stDateInput"] input,
[data-testid="stTimeInput"] input,
[data-baseweb="select"] > div {
  border-radius: 8px;
  border-color: rgba(35, 111, 167, 0.22);
  background: rgba(255, 255, 255, 0.95);
}

[data-testid="stTextInput"] input:focus,
[data-testid="stTextArea"] textarea:focus,
[data-testid="stNumberInput"] input:focus {
  border-color: var(--cm-blue-400);
  box-shadow: 0 0 0 3px rgba(127, 194, 243, 0.22);
}

[data-testid="stCheckbox"] label {
  min-height: 1.5rem;
}

[data-testid="stChatMessage"] {
  background: rgba(255, 255, 255, 0.84);
  border: 1px solid var(--cm-line);
  box-shadow: 0 10px 24px rgba(22, 50, 79, 0.07);
}

[data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
  background: rgba(220, 239, 255, 0.72);
}

[data-testid="stProgress"] > div > div > div {
  background: linear-gradient(90deg, var(--cm-blue-700), var(--cm-mint));
}

hr {
  border-color: rgba(35, 111, 167, 0.16);
}

code {
  color: var(--cm-blue-900);
  background: rgba(220, 239, 255, 0.8);
  border: 1px solid rgba(35, 111, 167, 0.14);
  border-radius: 6px;
}

.cm-home-hero {
  min-height: 12.5rem;
  padding: 1.45rem 1.6rem;
  border: 1px solid rgba(35, 111, 167, 0.16);
  border-radius: 8px;
  background:
    linear-gradient(135deg, rgba(255, 255, 255, 0.96) 0%, rgba(220, 239, 255, 0.94) 56%, rgba(200, 244, 235, 0.58) 100%);
  box-shadow: var(--cm-shadow);
}

.cm-home-hero h1 {
  border: 0;
  margin: 0.15rem 0 0.55rem;
  padding: 0;
}

.cm-home-hero p {
  max-width: 44rem;
  margin: 0;
  color: var(--cm-muted);
  font-size: 1.08rem;
  font-weight: 700;
}

.cm-kicker {
  color: var(--cm-blue-700);
  font-size: 0.78rem;
  font-weight: 800;
  letter-spacing: 0;
  text-transform: uppercase;
}

.cm-home-actions {
  padding-top: 0.15rem;
}

.cm-stat {
  min-height: 5rem;
  padding: 0.95rem 1rem;
  border: 1px solid var(--cm-line);
  border-radius: 8px;
  background: rgba(255, 255, 255, 0.86);
  box-shadow: 0 10px 22px rgba(22, 50, 79, 0.07);
}

.cm-stat span {
  display: block;
  color: var(--cm-navy);
  font-family: "Fraunces", Georgia, serif;
  font-size: 2rem;
  font-weight: 760;
  line-height: 1;
}

.cm-stat small {
  display: block;
  margin-top: 0.35rem;
  color: var(--cm-muted) !important;
  font-weight: 800;
}

.cm-pill,
.cm-source,
.cm-due {
  display: inline-flex;
  align-items: center;
  min-height: 1.8rem;
  max-width: 100%;
  padding: 0.24rem 0.55rem;
  border-radius: 999px;
  white-space: normal;
  line-height: 1.15;
  font-size: 0.84rem;
  font-weight: 800;
}

.cm-pill {
  color: var(--cm-blue-900);
  background: var(--cm-blue-100);
  border: 1px solid rgba(35, 111, 167, 0.16);
}

.cm-source {
  color: #31536f;
  background: #eef6ff;
  border: 1px solid rgba(49, 83, 111, 0.13);
}

.cm-due {
  color: #174c72;
  background: #e8f6ff;
  border: 1px solid rgba(23, 76, 114, 0.13);
}

.cm-due-overdue {
  color: #8a2f42;
  background: #fff0f4;
  border-color: rgba(138, 47, 66, 0.14);
}

.cm-task-title {
  min-height: 1.8rem;
  display: flex;
  align-items: center;
  color: var(--cm-ink);
  font-weight: 800;
  line-height: 1.25;
  overflow-wrap: anywhere;
}

.cm-strike {
  color: var(--cm-muted);
  text-decoration: line-through;
}

.cm-empty {
  padding: 1.2rem 1.3rem;
  border-radius: 8px;
  border: 1px dashed rgba(35, 111, 167, 0.28);
  background: rgba(255, 255, 255, 0.72);
}

.cm-empty strong,
.cm-empty span {
  display: block;
}

.cm-empty span {
  margin-top: 0.25rem;
  color: var(--cm-muted);
  font-weight: 700;
}

@media (max-width: 768px) {
  h1 {
    font-size: 2.2rem;
  }

  .cm-home-hero {
    min-height: 10.5rem;
    padding: 1.15rem 1.1rem;
  }

  .cm-stat {
    min-height: 4.4rem;
  }

  .main .block-container,
  [data-testid="stMainBlockContainer"] {
    padding-left: 0.9rem;
    padding-right: 0.9rem;
  }

  /* st.columns() stacks vertically below tablet-portrait width so nothing
     gets squeezed into unreadable slivers; wide screens are unaffected. */
  [data-testid="stHorizontalBlock"] {
    flex-direction: column !important;
  }

  [data-testid="stHorizontalBlock"] > [data-testid="stColumn"],
  [data-testid="stHorizontalBlock"] > [data-testid="column"] {
    width: 100% !important;
    min-width: 100% !important;
    flex: 1 1 100% !important;
  }

  /* Bigger touch targets (Apple HIG recommends 44px minimum). */
  div.stButton > button,
  div.stDownloadButton > button,
  [data-testid="stFormSubmitButton"] button {
    min-height: 2.75rem;
    padding: 0.5rem 1rem;
  }

  /* Tab bars normally scroll horizontally when tabs overflow (see
     .stTabs [data-baseweb="tab-list"] above), but iPadOS/Safari hide the
     scrollbar, so overflowing tabs (e.g. "Tutor") look like they've been
     cut off with no hint to scroll. Wrap onto a second row instead. */
  .stTabs [data-baseweb="tab-list"] {
    flex-wrap: wrap;
    overflow-x: visible;
  }

  [data-testid="stCheckbox"] label {
    min-height: 2rem;
  }

  .stTabs [data-baseweb="tab"] {
    height: 2.75rem;
    padding: 0.45rem 0.95rem;
  }

  /* Fixed-height chat/scroll containers (st.container(height=...)) become
     viewport-relative so the chat input isn't pushed off-screen behind the
     iOS keyboard. Matched by their known inline pixel heights. */
  div[style*="height: 520px"],
  div[style*="height: 480px"] {
    height: 58vh !important;
    max-height: 58vh !important;
  }

  /* Keep content clear of the iPhone home-indicator bar. */
  .main .block-container,
  [data-testid="stMainBlockContainer"] {
    padding-bottom: calc(4rem + env(safe-area-inset-bottom));
  }
}
</style>
        """,
        unsafe_allow_html=True,
    )
