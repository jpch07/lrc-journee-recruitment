from datetime import date
from decimal import Decimal

from app.assessment_runtime import active_assessment_definition, activate_assessment_definition, reset_assessment_definition
from app.db import SessionLocal
from app.management_corrections import build_override_map, correction_signature
from app.models import AdminEvaluation, ManagementCorrection, Recruit
from app.services import create_journey, result_snapshot
from app.utils import dumps


def test_missing_custom_scale_criteria_have_zero_normalized_contribution():
    from app.correction_scoring import criterion_facts
    definition = active_assessment_definition().model_copy(deep=True)
    activity = next(a for a in definition.activities if a.scoring != 'target_average')
    for criterion in activity.criteria:
        criterion.minimum, criterion.maximum = Decimal(1), Decimal(5)
    first, second = activity.criteria[:2]
    for policy in ('submitted_only', 'missing_as_zero'):
        definition.scoring.assessorAggregation = policy
        facts = criterion_facts(definition, {}, {}, {activity.key: 2},
                                {activity.key: {first.key: '0'}})[activity.key]
        assert facts[first.key]['_normalized'] == 0
        assert facts[second.key]['_normalized'] == 0
    facts = criterion_facts(definition, {activity.key: [{second.key: 5}]}, {},
                            {activity.key: 2}, {activity.key: {first.key: '0'}})[activity.key]
    assert facts[second.key]['_normalized'] == Decimal('.5')
    assert facts[second.key]['rawAverage'] == 5


def fixture(db):
    journey = create_journey(db, 'Correction test', date(2026, 9, 28), 1, 'Test')
    journey.status = 'completed'
    recruit = Recruit(journey_id=journey.id, name='Fictional Person', present=True)
    db.add(recruit)
    db.flush()
    definition = active_assessment_definition()
    for activity in definition.activities:
        db.add(AdminEvaluation(journey_id=journey.id, recruit_id=recruit.id, activity_code=activity.key,
                               responses_json=dumps({c.key: 2 for c in activity.criteria}),
                               raw_payload_json=dumps({'original': 99}), score=Decimal(2), updated_by='Original'))
    db.commit()
    return journey, recruit


def test_activity_override_preserves_automatic_and_raw_records():
    with SessionLocal() as db:
        journey, recruit = fixture(db)
        before = result_snapshot(db, journey)['rows'][0]
        definition = active_assessment_definition()
        activity = definition.activities[0]
        values = build_override_map(definition, {}, 'activity', activity.key, Decimal(4))
        correction = ManagementCorrection(system_id=journey.system_id, journey_id=journey.id, recruit_id=recruit.id,
            criterion_values_json=dumps(values), configuration_signature=correction_signature(definition), updated_by='Manager')
        db.add(correction)
        db.commit()
        row = result_snapshot(db, journey, include_criteria=True)['rows'][0]
        assert row['activities'][activity.key]['score'] == 4
        assert row['activities'][activity.key]['automaticScore'] == 2
        assert row['automaticScore'] == before['overallScore']
        assert row['activities'][activity.key]['submitted'] == 0
        assert row['criteria'][activity.key][activity.criteria[0].key]['effectiveAverage'] == 4
        for original in db.query(AdminEvaluation).all():
            assert original.score == 2
            assert original.raw_payload_json == '{"original":99}'


def test_color_does_not_change_any_score_or_rank():
    with SessionLocal() as db:
        journey, recruit = fixture(db)
        before = result_snapshot(db, journey)['rows'][0]
        definition = active_assessment_definition()
        color = definition.scoring.bands[-1].key
        db.add(ManagementCorrection(system_id=journey.system_id, journey_id=journey.id, recruit_id=recruit.id,
            color_key=color, configuration_signature=correction_signature(definition), updated_by='Manager'))
        db.commit()
        row = result_snapshot(db, journey)['rows'][0]
        assert row['color'] == color and row['manualColor']
        for field in ['overallScore', 'overallRank', 'activities', 'dimensions']:
            assert row[field] == before[field]


def test_manually_graded_missing_dimension_counts_without_fake_submissions():
    definition = active_assessment_definition().model_copy(deep=True)
    definition.scoring.missingComponents = 'exclude'
    token = activate_assessment_definition(definition)
    try:
        with SessionLocal() as db:
            journey = create_journey(db, 'Missing grades', date(2026, 9, 28), 1, 'Test')
            recruit = Recruit(journey_id=journey.id, name='Fictional missing', present=True)
            db.add(recruit); db.flush()
            values = {}
            for activity in definition.activities:
                values = build_override_map(definition, values, 'activity', activity.key, Decimal(4))
            db.add(ManagementCorrection(system_id=journey.system_id, journey_id=journey.id, recruit_id=recruit.id,
                criterion_values_json=dumps(values), configuration_signature=correction_signature(definition), updated_by='Manager'))
            db.commit()
            row = result_snapshot(db, journey)['rows'][0]
            assert row['overallScore'] == float(definition.scoring.officialMaximum * Decimal('.8'))
            assert all(a['submitted'] == 0 and not a['complete'] and a['manuallyGraded'] for a in row['activities'].values())
    finally:
        reset_assessment_definition(token)


def test_overlapping_dimension_recalculates_activity_and_export():
    from app.report_exports import build_management_report_workbook
    with SessionLocal() as db:
        journey, recruit = fixture(db)
        definition = active_assessment_definition()
        dimension = next(d for d in definition.dimensions if d.source == 'criteria')
        activity = next(a for a in definition.activities if any(c.dimensionKey == dimension.key for c in a.criteria))
        values = build_override_map(definition, {}, 'activity', activity.key, Decimal(4))
        values = build_override_map(definition, values, 'dimension', dimension.key, Decimal(1))
        db.add(ManagementCorrection(system_id=journey.system_id, journey_id=journey.id, recruit_id=recruit.id,
            criterion_values_json=dumps(values), configuration_signature=correction_signature(definition), updated_by='Manager'))
        db.commit()
        row = result_snapshot(db, journey)['rows'][0]
        assert row['dimensions'][dimension.key]['score'] == .2
        assert row['activities'][activity.key]['score'] < 4
        book = build_management_report_workbook(db)
        assert 'Management corrections' in book.sheetnames
        sheet = book['Management corrections']
        assert sheet.cell(2, 4).value == row['overallScore']
