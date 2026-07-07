from dataclasses import dataclass, field
from typing import Optional


@dataclass
class Talent:
    id: Optional[int] = None
    name: str = ""
    domain: str = "music"  # 'music' or 'actor'
    external_ids_json: str = "{}"
    genres_json: str = "[]"
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


@dataclass
class Performance:
    id: Optional[int] = None
    talent_id: int = 0
    venue_name: str = ""
    city: str = ""
    estimated_date: str = ""
    target_capacity: int = 0
    budget: float = 0.0
    assumed_ticket_price: Optional[float] = None
    assumed_sell_through_rate: Optional[float] = None
    notes: str = ""
    created_at: Optional[str] = None


@dataclass
class HistoricalComp:
    id: Optional[int] = None
    talent_id: Optional[int] = None
    comparable_name: str = ""
    is_self: bool = True
    domain: str = "music"
    venue_name: str = ""
    city: str = ""
    event_date: str = ""
    capacity: Optional[int] = None
    attendance: Optional[int] = None
    ticket_price_avg: Optional[float] = None
    gross_revenue: Optional[float] = None
    talent_fee: Optional[float] = None
    total_expenses: Optional[float] = None
    source: str = "manual"
    created_at: Optional[str] = None


@dataclass
class ExpenseTemplate:
    id: Optional[int] = None
    name: str = "Default"
    venue_pct: float = 0.25
    marketing_pct: float = 0.15
    production_pct: float = 0.20
    talent_fee_pct: float = 0.30
    other_pct: float = 0.10
    is_default: bool = True
