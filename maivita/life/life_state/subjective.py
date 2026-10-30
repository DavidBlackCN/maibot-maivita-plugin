"""Versioned bounded estimates; sleep debt is recomputed, never accumulated."""
from dataclasses import replace
from datetime import timedelta
import logging
import math

from ..domain.clock import local_day_bounds, to_local
from ..domain.models import number, require

RULE_VERSION = 'fixed-categories-v4'
logger = logging.getLogger(__name__)


def body_value_warning(field, value):
    """Observation only: extreme estimates are not proof of corrupted data."""
    if value is None:
        return None
    if not math.isfinite(value) or not 0 <= value <= 100:
        return 'out_of_range'
    if (field in ('stress', 'fatigue') and value > 95) or (field == 'energy' and value < 5):
        return 'extreme_value'
    return None


def sleep_debt(segments, *, now, timezone, observed_since, need_minutes=480, observed_intervals=None):
    number(need_minutes, 0, 1440)
    total, coverage = 0.0, 0
    today = to_local(now, timezone).date()
    for offset in range(1, 8):
        start, end = local_day_bounds(today - timedelta(days=offset), timezone)
        # Only completely observed local days form the coverage denominator.
        if start < observed_since or end > now:
            continue
        if observed_intervals is not None:
            watermark=start
            for left,right in sorted(observed_intervals):
                if left>watermark: break
                if right>watermark: watermark=right
                if watermark>=end: break
            if watermark<end: continue  # gaps are unknown, never zero sleep
        slept = 0.0
        for segment in segments:
            if segment.kind != 'sleep' or segment.ended_at is None:
                continue
            overlap = min(segment.ended_at, end) - max(segment.started_at, start)
            slept += max(0, overlap.total_seconds() / 60)
        total += max(need_minutes - slept, 0)
        coverage += 1
    return (total if coverage else None), coverage


def advance(u, state, now, payload):
    require(now >= state.last_advanced_at, 'advance clock regressed')
    for field in ('energy', 'fatigue', 'stress'):
        current = getattr(state, field)
        reason = body_value_warning(field, current)
        if reason:
            logger.warning('body_state anomaly character_id=%s field=%s current=%s reason=%s',
                           state.character_id, field, current, reason)
        # Preserve the domain's rejection of invalid inputs, never clamp them
        # into apparently valid history. Legitimate extremes still evolve.
        if current is not None:
            number(current, 0, 100)
    # Large unknown gaps are not physiological observations. Bound the rate
    # integration and preserve uncertainty instead of fabricating full days.
    start=max(state.last_advanced_at,now-timedelta(hours=1))
    cutover=u.connection.execute('SELECT applied_at_ms FROM body_rule_cutovers WHERE rule_version=?',(RULE_VERSION,)).fetchone()
    if cutover:
        from ..domain.clock import from_epoch_ms
        start=max(start,from_epoch_ms(cutover[0]))
    delta=[0.0,0.0,0.0]
    activities=[a for a in u.activities.for_character(state.character_id) if a.role=='primary']
    segments=u.segments.for_character(state.character_id)
    sleeping_starts={(s.session_id,s.started_at) for s in segments if s.kind=='sleep'}
    from ..domain.clock import from_epoch_ms
    gaps=[(from_epoch_ms(row[0]),from_epoch_ms(row[1])) for row in u.connection.execute('SELECT start_at_ms,end_at_ms FROM observation_gaps WHERE character_id=?',(state.character_id,))]
    for actual in activities:
        hours=max(0,(min(actual.ended_at or now,now)-max(actual.started_at,start)).total_seconds()/3600)
        for left,right in gaps:
            hours-=max(0,(min(actual.ended_at or now,now,right)-max(actual.started_at,start,left)).total_seconds()/3600)
        hours=max(0,hours)
        sleeping=(actual.sleep_session_id,actual.started_at) in sleeping_starts
        demanding=actual.category in ('study','work','travel','creative')
        rates=(8,-5,-3) if sleeping else (3,-2,-1) if actual.category=='rest' else (-3,2,0.8) if demanding else (-1,1,-1)
        for index,rate in enumerate(rates): delta[index]+=hours*rate
    values = {}
    for field, change in zip(('energy','fatigue','stress'), delta):
        current = getattr(state, field)
        if not math.isfinite(change) or abs(change) > 50:
            logger.warning('body_state anomaly character_id=%s field=%s current=%s delta=%s reason=abnormal_delta',
                           state.character_id, field, current, change)
        values[field] = None if current is None else max(0, min(100, current + change))
    # Initialization is explicit and labelled a low-confidence estimate.
    if payload.get('initialize_neutral'):
        values = {key: 50 if value is None else value for key,value in values.items()}
    intervals=[(a.started_at,a.ended_at) for a in activities if a.ended_at is not None
        and not any(a.started_at<right and a.ended_at>left for left,right in gaps)]
    observed_since=min((left for left,right in intervals),default=now)
    debt, coverage = sleep_debt(segments, now=now,
        timezone=payload.get('timezone','Asia/Shanghai'), observed_since=observed_since,
        need_minutes=payload.get('sleep_need_minutes',480),observed_intervals=intervals)
    return replace(state, **values, last_advanced_at=now, sleep_debt_minutes=debt,
        sleep_history_coverage_days=coverage, provenance=RULE_VERSION, confidence='low',
        degraded_reason='history_incomplete' if coverage < 7 else None)
