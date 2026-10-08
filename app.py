
import streamlit as st
import pandas as pd
import sqlite3
import io
import hmac
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

# =========================================================
# 1. WEBSITE CONFIGURATION
# =========================================================

st.set_page_config(
    page_title="نظام متابعة وتوزيع الإصدارات",
    page_icon="📚",
    layout="wide"
)

TZ = ZoneInfo("Asia/Dubai")
DB_FILE = "books_inventory.db"

# =========================================================
# 2. BOOK TITLES AND INITIAL STOCK
# =========================================================

BOOKS = {
    "كتيب الاستزراع السمكي (عربي - عادي)": 710,
    "كتيب الاستزراع السمكي (إنجليزي - عادي)": 381,
    "كتيب الاستزراع السمكي (إنجليزي - VIP)": 107,
    "كتيب الاستزراع السمكي (عربي - VIP)": 0,
    "كتيب الاستزراع السمكي (عربي - VVIP)": 10,
    "دليل الأسماك المحلية (عربي - عادي)": 947,
    "دليل الأسماك المحلية (إنجليزي - عادي)": 0,
    "دليل الأسماك المحلية (عربي - VIP)": 16,
    "دليل الأسماك المحلية (إنجليزي - VIP)": 0
}

MONTHS = [
    "يناير", "فبراير", "مارس", "أبريل",
    "مايو", "يونيو", "يوليو", "أغسطس",
    "سبتمبر", "أكتوبر", "نوفمبر", "ديسمبر"
]

# =========================================================
# 3. LIGHT BLUE WEBSITE DESIGN
# =========================================================

st.markdown("""
<style>

.stApp {
    background-color: #EAF8FF;
    direction: rtl;
}

h1, h2, h3, p, label {
    font-family: Tahoma, Arial, sans-serif;
    color: #174A68;
}

h1 {
    text-align: center;
    font-weight: 800;
}

[data-testid="stMetric"] {
    background-color: white;
    border: 1px solid #B8E2F5;
    border-radius: 15px;
    padding: 18px;
    box-shadow: 0px 3px 10px rgba(0,0,0,0.04);
}

[data-testid="stForm"] {
    background-color: white;
    border: 1px solid #B8E2F5;
    border-radius: 15px;
    padding: 20px;
}

.stButton > button,
.stDownloadButton > button,
[data-testid="stFormSubmitButton"] button {
    background-color: #1689B5;
    color: white;
    border-radius: 10px;
    border: none;
    font-weight: bold;
}

.stButton > button:hover,
.stDownloadButton > button:hover {
    background-color: #0C6F96;
    color: white;
}

[data-testid="stTabs"] {
    background-color: transparent;
}

</style>
""", unsafe_allow_html=True)

# =========================================================
# 4. DATABASE
# =========================================================

def connect_db():
    conn = sqlite3.connect(DB_FILE, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def initialize_database():

    with connect_db() as conn:

        conn.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            book_title TEXT PRIMARY KEY,
            initial_quantity INTEGER NOT NULL
        )
        """)

        conn.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            book_title TEXT NOT NULL,
            operation TEXT NOT NULL,
            quantity INTEGER NOT NULL,
            recipient TEXT,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """)

        # Insert initial stock only once
        for title, quantity in BOOKS.items():

            conn.execute("""
            INSERT OR IGNORE INTO inventory
            (book_title, initial_quantity)
            VALUES (?, ?)
            """, (title, quantity))

        conn.commit()


initialize_database()

# =========================================================
# 5. LOAD TRANSACTIONS
# =========================================================

def get_transactions():

    with connect_db() as conn:

        df = pd.read_sql_query("""
        SELECT *
        FROM transactions
        ORDER BY date DESC, id DESC
        """, conn)

    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])

    return df


def get_inventory():

    with connect_db() as conn:

        inventory = pd.read_sql_query("""
        SELECT book_title, initial_quantity
        FROM inventory
        """, conn)

    transactions = get_transactions()

    inventory["distributed"] = 0
    inventory["returned"] = 0
    inventory["added"] = 0

    if not transactions.empty:

        for index, row in inventory.iterrows():

            title = row["book_title"]

            book_tx = transactions[
                transactions["book_title"] == title
            ]

            inventory.loc[index, "distributed"] = (
                book_tx[
                    book_tx["operation"] == "تسليم"
                ]["quantity"].sum()
            )

            inventory.loc[index, "returned"] = (
                book_tx[
                    book_tx["operation"] == "إرجاع"
                ]["quantity"].sum()
            )

            inventory.loc[index, "added"] = (
                book_tx[
                    book_tx["operation"] == "إضافة مخزون"
                ]["quantity"].sum()
            )

    inventory["remaining"] = (
        inventory["initial_quantity"]
        - inventory["distributed"]
        + inventory["returned"]
        + inventory["added"]
    )

    return inventory


# =========================================================
# 6. SAVE TRANSACTION
# =========================================================

def save_transaction(
    date,
    book_title,
    operation,
    quantity,
    recipient,
    notes
):

    with connect_db() as conn:

        conn.execute("BEGIN IMMEDIATE")

        initial = conn.execute("""
        SELECT initial_quantity
        FROM inventory
        WHERE book_title = ?
        """, (book_title,)).fetchone()

        if initial is None:
            raise ValueError("الإصدار غير موجود")

        movements = conn.execute("""
        SELECT COALESCE(
            SUM(
                CASE
                    WHEN operation = 'تسليم'
                    THEN -quantity
                    ELSE quantity
                END
            ), 0
        )
        FROM transactions
        WHERE book_title = ?
        """, (book_title,)).fetchone()[0]

        remaining = int(initial[0]) + int(movements)

        if operation == "تسليم" and quantity > remaining:
            raise ValueError(
                f"الكمية المطلوبة أكبر من المخزون. "
                f"المتوفر: {remaining}"
            )

        conn.execute("""
        INSERT INTO transactions
        (
            date,
            book_title,
            operation,
            quantity,
            recipient,
            notes
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            date,
            book_title,
            operation,
            quantity,
            recipient,
            notes
        ))

        conn.commit()


# =========================================================
# 7. EXPORT EXCEL
# =========================================================

def export_excel(df):

    output = io.BytesIO()

    with pd.ExcelWriter(
        output,
        engine="openpyxl"
    ) as writer:

        df.to_excel(
            writer,
            sheet_name="Report",
            index=False
        )

        worksheet = writer.sheets["Report"]
        worksheet.sheet_view.rightToLeft = True

        for column in worksheet.columns:

            max_length = max(
                len(str(cell.value or ""))
                for cell in column
            )

            letter = column[0].column_letter

            worksheet.column_dimensions[letter].width = min(
                max_length + 3, 60
            )

    return output.getvalue()


# =========================================================
# 8. WEBSITE HEADER AND AUTHORITY LOGO
# =========================================================

logo = Path("logo.png")

if logo.exists():

    left, center, right = st.columns([2, 2, 2])

    with center:
        st.image(str(logo), use_container_width=True)

st.title("نظام متابعة وتوزيع الإصدارات")

st.markdown(
    "<h4 style='text-align:center;color:#357A9A;'>"
    "هيئة الشارقة للثروة السمكية"
    "</h4>",
    unsafe_allow_html=True
)

st.divider()

# =========================================================
# 9. DASHBOARD
# =========================================================

inventory = get_inventory()
transactions = get_transactions()

now = datetime.now(TZ)

total_initial = int(inventory["initial_quantity"].sum())
total_remaining = int(inventory["remaining"].sum())

if not transactions.empty:

    monthly_tx = transactions[
        (transactions["date"].dt.month == now.month)
        & (transactions["date"].dt.year == now.year)
    ]

    monthly_given = int(
        monthly_tx[
            monthly_tx["operation"] == "تسليم"
        ]["quantity"].sum()
    )

    monthly_returned = int(
        monthly_tx[
            monthly_tx["operation"] == "إرجاع"
        ]["quantity"].sum()
    )

else:

    monthly_given = 0
    monthly_returned = 0

col1, col2, col3, col4 = st.columns(4)

col1.metric(
    "إجمالي المخزون الحالي",
    f"{total_remaining:,}"
)

col2.metric(
    "الكتب المسلّمة هذا الشهر",
    f"{monthly_given:,}"
)

col3.metric(
    "الكتب المُرجعة هذا الشهر",
    f"{monthly_returned:,}"
)

col4.metric(
    "عدد أنواع الإصدارات",
    len(BOOKS)
)

st.divider()

# =========================================================
# 10. TABS
# =========================================================

tab1, tab2, tab3, tab4 = st.tabs([
    "📝 تسجيل حركة",
    "📊 التقرير الشهري",
    "📚 المخزون الحالي",
    "📋 سجل الحركات"
])

# =========================================================
# TAB 1 - REGISTER BOOK MOVEMENT
# =========================================================

# =========================================================
# TAB 1 - REGISTER BOOK MOVEMENT
# =========================================================

with tab1:

    st.subheader("تسجيل حركة جديدة")

    # Select the book OUTSIDE the form
    # This allows the available quantity to update immediately
    book_title = st.selectbox(
        "اختر الإصدار",
        list(BOOKS.keys()),
        key="selected_book"
    )

    # Get the latest available quantity
    current_inventory = get_inventory()

    available = int(
        current_inventory.loc[
            current_inventory["book_title"] == book_title,
            "remaining"
        ].iloc[0]
    )

    # Display available stock
    st.markdown(
        f"""
        <div style="
            background-color: #E7F2FF;
            padding: 18px;
            border-radius: 10px;
            margin-top: 15px;
            margin-bottom: 15px;
            text-align: right;
            direction: rtl;
            color: #124B70;
            font-size: 18px;
            border: 1px solid #C8E5FF;
        ">
            الكمية المتوفرة حالياً:
            <strong>{available:,} نسخة</strong>
        </div>
        """,
        unsafe_allow_html=True
    )

    # Transaction form
    with st.form("transaction_form", clear_on_submit=True):

        operation = st.selectbox(
            "نوع الحركة",
            ["تسليم", "إرجاع", "إضافة مخزون"]
        )

        quantity = st.number_input(
            "عدد النسخ",
            min_value=1,
            value=1,
            step=1
        )

        recipient = st.text_input(
            "اسم المستلم / الجهة"
        )

        notes = st.text_area(
            "ملاحظات إضافية"
        )

        c1, c2 = st.columns(2)

        with c1:
            date = st.date_input(
                "تاريخ الحركة",
                value=now.date()
            )

        with c2:
            time = st.time_input(
                "وقت الحركة",
                value=now.time().replace(
                    second=0,
                    microsecond=0
                )
            )

        submitted = st.form_submit_button(
            "حفظ الحركة",
            use_container_width=True
        )

    # Save the movement
    if submitted:

        timestamp = datetime.combine(date, time)

        try:

            save_transaction(
                timestamp.isoformat(),
                book_title,
                operation,
                int(quantity),
                recipient,
                notes
            )

            st.success("تم تسجيل الحركة بنجاح")
            st.rerun()

        except Exception as error:
            st.error(str(error))

# =========================================================
# TAB 2 - MONTHLY REPORT
# =========================================================

with tab2:

    st.subheader("التقرير الشهري لتوزيع الإصدارات")

    c1, c2 = st.columns(2)

    with c1:

        year = st.number_input(
            "السنة",
            min_value=2020,
            max_value=2100,
            value=now.year,
            step=1
        )

    with c2:

        month = st.selectbox(
            "الشهر",
            range(1, 13),
            index=now.month - 1,
            format_func=lambda x: MONTHS[x - 1]
        )

    if not transactions.empty:

        selected_tx = transactions[
            (transactions["date"].dt.year == year)
            & (transactions["date"].dt.month == month)
        ]

    else:

        selected_tx = transactions

    report_rows = []

    for book in BOOKS:

        book_transactions = selected_tx[
            selected_tx["book_title"] == book
        ]

        given = int(
            book_transactions[
                book_transactions["operation"] == "تسليم"
            ]["quantity"].sum()
        )

        returned = int(
            book_transactions[
                book_transactions["operation"] == "إرجاع"
            ]["quantity"].sum()
        )

        report_rows.append({
            "عنوان الإصدار": book,
            "الكتب المسلّمة": given,
            "الكتب المُرجعة": returned,
            "صافي التوزيع": given - returned
        })

    monthly_report = pd.DataFrame(report_rows)

    total_row = pd.DataFrame([{
        "عنوان الإصدار": "الإجمالي",
        "الكتب المسلّمة": monthly_report[
            "الكتب المسلّمة"
        ].sum(),
        "الكتب المُرجعة": monthly_report[
            "الكتب المُرجعة"
        ].sum(),
        "صافي التوزيع": monthly_report[
            "صافي التوزيع"
        ].sum()
    }])

    monthly_report = pd.concat(
        [monthly_report, total_row],
        ignore_index=True
    )

    st.dataframe(
        monthly_report,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "📥 تحميل التقرير الشهري Excel",
        data=export_excel(monthly_report),
        file_name=f"Monthly_Report_{year}_{month}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.divider()

    st.subheader("ملخص جميع أشهر السنة")

    yearly_rows = []

    for m in range(1, 13):

        if not transactions.empty:

            month_df = transactions[
                (transactions["date"].dt.year == year)
                & (transactions["date"].dt.month == m)
            ]

        else:

            month_df = transactions

        given = int(
            month_df[
                month_df["operation"] == "تسليم"
            ]["quantity"].sum()
        )

        returned = int(
            month_df[
                month_df["operation"] == "إرجاع"
            ]["quantity"].sum()
        )

        yearly_rows.append({
            "الشهر": MONTHS[m - 1],
            "الكتب المسلّمة": given,
            "الكتب المُرجعة": returned,
            "صافي التوزيع": given - returned
        })

    yearly_report = pd.DataFrame(yearly_rows)

    st.dataframe(
        yearly_report,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "📥 تحميل التقرير السنوي",
        data=export_excel(yearly_report),
        file_name=f"Yearly_Report_{year}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )


# =========================================================
# TAB 3 - INVENTORY
# =========================================================

with tab3:

    st.subheader("المخزون الحالي للإصدارات")

    inventory_display = inventory.rename(columns={
        "book_title": "عنوان الإصدار",
        "initial_quantity": "المخزون الافتتاحي",
        "distributed": "إجمالي النسخ المسلّمة",
        "returned": "إجمالي النسخ المُرجعة",
        "added": "إضافات المخزون",
        "remaining": "المخزون المتبقي"
    })

    st.dataframe(
        inventory_display,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "📥 تحميل تقرير المخزون",
        data=export_excel(inventory_display),
        file_name="Current_Inventory.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    st.divider()

    st.subheader("توزيع المخزون حسب الإصدار")

    chart_df = inventory.set_index("book_title")[
        ["remaining"]
    ]

    st.bar_chart(chart_df)


# =========================================================
# TAB 4 - TRANSACTION HISTORY
# =========================================================

with tab4:

    st.subheader("سجل جميع الحركات")

    filter_operation = st.selectbox(
        "تصفية حسب نوع الحركة",
        ["جميع الحركات", "تسليم", "إرجاع", "إضافة مخزون"]
    )

    filter_book = st.selectbox(
        "تصفية حسب الإصدار",
        ["جميع الإصدارات"] + list(BOOKS.keys())
    )

    filtered = transactions.copy()

    if filter_operation != "جميع الحركات":

        filtered = filtered[
            filtered["operation"] == filter_operation
        ]

    if filter_book != "جميع الإصدارات":

        filtered = filtered[
            filtered["book_title"] == filter_book
        ]

    if not filtered.empty:

        filtered_display = filtered[[
            "date",
            "book_title",
            "operation",
            "quantity",
            "recipient",
            "notes"
        ]].copy()

        filtered_display.columns = [
            "التاريخ",
            "عنوان الإصدار",
            "نوع الحركة",
            "العدد",
            "المستلم / الجهة",
            "ملاحظات"
        ]

    else:

        filtered_display = pd.DataFrame(columns=[
            "التاريخ",
            "عنوان الإصدار",
            "نوع الحركة",
            "العدد",
            "المستلم / الجهة",
            "ملاحظات"
        ])

    st.dataframe(
        filtered_display,
        use_container_width=True,
        hide_index=True
    )

    st.download_button(
        "📥 تحميل سجل الحركات",
        data=export_excel(filtered_display),
        file_name="Book_Transactions.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    )

    # =========================================================
    # ADMINISTRATOR ONLY - LOCKED TRANSACTION DELETION
    # =========================================================

    st.divider()

    with st.expander("🔒 حذف حركة مسجلة - للمسؤول فقط"):

        st.warning(
            "هذه الخاصية مخصصة للمسؤول فقط. "
            "سيؤدي حذف الحركة إلى تحديث المخزون تلقائياً."
        )

        # Read administrator password from Streamlit Secrets
        try:
            correct_password = str(
                st.secrets["ADMIN_PASSWORD"]
            )
        except (KeyError, FileNotFoundError):
            correct_password = ""

        if not correct_password:

            st.error(
                "لم يتم إعداد كلمة مرور المسؤول. "
                "يرجى إضافتها إلى Streamlit Secrets."
            )

        else:

            # Password input
            entered_password = st.text_input(
                "كلمة مرور المسؤول",
                type="password",
                key="admin_delete_password"
            )

            # Verify administrator password
            is_admin = (
                bool(entered_password)
                and hmac.compare_digest(
                    entered_password,
                    correct_password
                )
            )

            if not is_admin:

                st.info(
                    "🔐 أدخل كلمة مرور المسؤول "
                    "لعرض خيارات حذف الحركات."
                )

            else:

                st.success(
                    "تم التحقق من كلمة مرور المسؤول."
                )

                if transactions.empty:

                    st.info(
                        "لا توجد حركات مسجلة للحذف."
                    )

                else:

                    # Build transaction selection list
                    transaction_options = {}

                    for _, row in transactions.iterrows():

                        transaction_id = int(row["id"])

                        label = (
                            f"رقم الحركة: {transaction_id} | "
                            f"{row['date']} | "
                            f"{row['book_title']} | "
                            f"{row['operation']} | "
                            f"{int(row['quantity'])} نسخة"
                        )

                        transaction_options[label] = (
                            transaction_id
                        )

                    selected_transaction = st.selectbox(
                        "اختر الحركة التي ترغب في حذفها",
                        options=list(
                            transaction_options.keys()
                        ),
                        key="admin_transaction_select"
                    )

                    selected_id = transaction_options[
                        selected_transaction
                    ]

                    # Display selected transaction details
                    selected_row = transactions[
                        transactions["id"] == selected_id
                    ].iloc[0]

                    st.markdown("#### تفاصيل الحركة المحددة")

                    st.write(
                        f"**الإصدار:** "
                        f"{selected_row['book_title']}"
                    )

                    st.write(
                        f"**نوع الحركة:** "
                        f"{selected_row['operation']}"
                    )

                    st.write(
                        f"**عدد النسخ:** "
                        f"{int(selected_row['quantity'])}"
                    )

                    st.write(
                        f"**التاريخ:** "
                        f"{selected_row['date']}"
                    )

                    st.write(
                        f"**المستلم / الجهة:** "
                        f"{selected_row['recipient'] or '-'}"
                    )

                    st.divider()

                    # Additional confirmation
                    confirm_delete = st.checkbox(
                        "أؤكد أنني أرغب في حذف "
                        "هذه الحركة نهائياً",
                        key=(
                            "confirm_delete_"
                            + str(selected_id)
                        )
                    )

                    # Delete button
                    if st.button(
                        "🗑️ حذف الحركة المحددة",
                        type="primary",
                        disabled=not confirm_delete,
                        use_container_width=True,
                        key="admin_delete_button"
                    ):

                        # Recheck password at deletion time
                        if not hmac.compare_digest(
                            entered_password,
                            correct_password
                        ):

                            st.error(
                                "غير مصرح بإجراء الحذف."
                            )

                        else:

                            try:

                                with connect_db() as conn:

                                    conn.execute(
                                        "BEGIN IMMEDIATE"
                                    )

                                    # Verify that transaction exists
                                    existing = conn.execute(
                                        """
                                        SELECT id
                                        FROM transactions
                                        WHERE id = ?
                                        """,
                                        (selected_id,)
                                    ).fetchone()

                                    if existing is None:

                                        raise ValueError(
                                            "الحركة غير موجودة."
                                        )

                                    # Delete selected transaction
                                    conn.execute(
                                        """
                                        DELETE FROM transactions
                                        WHERE id = ?
                                        """,
                                        (selected_id,)
                                    )

                                    conn.commit()

                                st.success(
                                    "تم حذف الحركة بنجاح، "
                                    "وسيتم تحديث المخزون."
                                )

                                st.rerun()

                            except Exception as error:

                                st.error(
                                    f"تعذر حذف الحركة: {error}"
                                )

# =========================================================
# FOOTER
# =========================================================

st.divider()

st.markdown(
    """
    <div style="
        text-align:center;
        color:#357A9A;
        padding:20px;
        font-size:13px;
    ">
    هيئة الشارقة للثروة السمكية
    <br>
    نظام متابعة وتوزيع الإصدارات
    </div>
    """,
    unsafe_allow_html=True
)
