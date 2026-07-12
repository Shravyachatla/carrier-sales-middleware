import pytest

from app.services.tms_client import TMSClient, TMSProtocolError, _TransportFault


@pytest.fixture
def client():
    c = TMSClient()
    c.token = "t-9c3a..."
    return c


def test_encode_request_matches_spec_format(client):
    line = client._encode_request("LOAD_QUERY", {"ORIG_STATE": "GA", "EQTYPE": "DRY_VAN"})
    assert line == b"CMD:LOAD_QUERY|AUTH:t-9c3a...|ORIG_STATE:GA|EQTYPE:DRY_VAN\r\n"


def test_encode_request_rejects_pipe_in_value(client):
    with pytest.raises(ValueError):
        client._encode_request("LOAD_QUERY", {"ORIG_CITY": "Atlanta|Fake"})


def test_parse_line_strips_padding_and_splits_fields(client):
    # From spec transcript: fixed-width padded value before the next delimiter
    line = "LOAD_ID:LD00000045821|ORIG_CITY:Atlanta                                          |ORIG_STATE:GA"
    fields = client._parse_line(line)
    assert fields["LOAD_ID"] == "LD00000045821"
    assert fields["ORIG_CITY"] == "Atlanta"  # trailing padding stripped
    assert fields["ORIG_STATE"] == "GA"


def test_parse_line_blank_notes_collapses_to_empty_string(client):
    # Spec: "Adapters that strip trailing whitespace will collapse blank NOTES to an empty string"
    line = "LOAD_ID:LD1|NOTES:                    "
    fields = client._parse_line(line)
    assert fields["NOTES"] == ""


def test_parse_line_malformed_field_raises_transport_fault(client):
    with pytest.raises(_TransportFault):
        client._parse_line("LOAD_ID-LD1|missing_colon_field")


def test_location_fields_heuristic_state_vs_zip_vs_city(client):
    assert client._location_fields("ORIG", "GA") == {"ORIG_STATE": "GA"}
    assert client._location_fields("ORIG", "30303") == {"ORIG_ZIP": "30303"}
    assert client._location_fields("ORIG", "Atlanta") == {"ORIG_CITY": "Atlanta"}


def test_parse_summary_maps_spec_fields_correctly(client):
    record = {
        "LOAD_ID": "LD00000045821",
        "ORIG_CITY": "Atlanta",
        "ORIG_STATE": "GA",
        "ORIG_ZIP": "30303",
        "DEST_CITY": "Dallas",
        "EQTYPE": "DRY_VAN",
    }
    summary = client._parse_summary(record)
    assert summary.load_id == "LD00000045821"
    assert summary.origin.city == "Atlanta"
    assert summary.origin.state == "GA"
    assert summary.destination.city == "Dallas"
    assert summary.equipment_type == "DRY_VAN"


def test_parse_detail_extracts_max_buy_as_ceiling_when_present(client):
    record = {
        "LOAD_ID": "LD1", "ORIG_CITY": "Atlanta", "DEST_CITY": "Dallas",
        "RATE": "2000", "MAX_BUY": "2200", "COMMODITY": "Steel", "DIMS": "48x48x48", "NOTES": "",
    }
    detail, max_rate = client._parse_detail(record)
    assert max_rate == 2200.0
    assert detail.loadboard_rate == 2000.0
    assert "MAX_BUY" not in detail.model_dump()  # never exposed on the model itself


def test_parse_detail_falls_back_to_rate_when_max_buy_absent(client):
    # Spec: MAX_BUY absent on tokens not flagged for it
    record = {"LOAD_ID": "LD1", "ORIG_CITY": "Atlanta", "DEST_CITY": "Dallas", "RATE": "1800"}
    detail, max_rate = client._parse_detail(record)
    assert max_rate == 1800.0  # conservative fallback: never exceed posted rate


def test_parse_detail_unmapped_fields_land_in_raw_fields(client):
    record = {"LOAD_ID": "LD1", "ORIG_CITY": "Atlanta", "DEST_CITY": "Dallas", "SOME_NEW_FIELD": "xyz"}
    detail, _ = client._parse_detail(record)
    assert detail.raw_fields == {"SOME_NEW_FIELD": "xyz"}


def test_tms_protocol_error_carries_code_and_message():
    err = TMSProtocolError("ALREADY_BOOKED", "load not available")
    assert err.code == "ALREADY_BOOKED"
    assert err.message == "load not available"
