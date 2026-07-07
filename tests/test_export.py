import openpyxl
from io import BytesIO

import export


def _sample_args():
    return dict(
        talent={"name": "Test Artist", "domain": "music"},
        performance={
            "venue_name": "Test Hall", "city": "Austin", "estimated_date": "2026-01-01",
            "target_capacity": 1000, "budget": 50000.0, "notes": "some notes",
        },
        revenue_info={
            "ticket_price": 45.0, "ticket_price_source": "default",
            "sell_through_rate": 0.75, "sell_through_rate_source": "default",
            "estimated_attendance": 750, "estimated_revenue": 33750.0,
        },
        expense_info={
            "total_expenses": 40000.0,
            "breakdown": {"venue": 10000.0, "marketing": 6000.0, "production": 8000.0,
                          "talent_fee": 12000.0, "other": 4000.0},
        },
        net_margin=-6250.0,
        venue_fit_score=0.75,
        marketing_efficiency=8.0,
        demand={"local_fan_density": 42.0, "merch_revenue_total": 1500.0},
        merch_per_attendee=2.0,
        similar=[{"comparable_name": "Other Artist", "avg_capacity": 900}],
        historical_summary={"in_city": [{"city": "Austin"}], "elsewhere": []},
    )


def test_build_booking_workbook_produces_all_expected_sheets():
    xlsx_bytes = export.build_booking_workbook(**_sample_args())
    wb = openpyxl.load_workbook(BytesIO(xlsx_bytes))
    assert wb.sheetnames == [
        "Booking summary", "Expense breakdown", "Demand metrics",
        "Similar talent", "History - in city", "History - elsewhere",
    ]


def test_build_booking_workbook_summary_sheet_has_key_fields():
    xlsx_bytes = export.build_booking_workbook(**_sample_args())
    wb = openpyxl.load_workbook(BytesIO(xlsx_bytes))
    ws = wb["Booking summary"]
    fields = [row[0] for row in ws.iter_rows(min_row=2, values_only=True)]
    assert "Talent name" in fields
    assert "Estimated revenue ($)" in fields
    assert "Venue fit score" in fields


def test_build_booking_workbook_handles_empty_demand_and_similar():
    args = _sample_args()
    args["demand"] = {}
    args["merch_per_attendee"] = None
    args["similar"] = []
    xlsx_bytes = export.build_booking_workbook(**args)
    wb = openpyxl.load_workbook(BytesIO(xlsx_bytes))
    demand_ws = wb["Demand metrics"]
    assert demand_ws.cell(row=2, column=1).value == "(none entered)"
