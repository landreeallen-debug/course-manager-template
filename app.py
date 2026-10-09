"""App entry point — defines navigation and global page config."""
import streamlit as st

import backup
from ui_style import apply_blue_theme

st.set_page_config(
    page_title="Course Manager",
    layout="wide",
)
apply_blue_theme()

with st.sidebar:
    st.download_button(
        "Export backup",
        data=backup.build_backup_zip(),
        file_name="course_manager_backup.zip",
        mime="application/zip",
        help="Download notes, assignments, grades, tutor history, course materials, and uploaded pages.",
    )

pg = st.navigation([
    st.Page("pages/0_Home.py",        title="Home"),
    st.Page("pages/1_Classes.py",     title="Classes"),
    st.Page("pages/3_Planning.py",    title="Planning"),
])
pg.run()
