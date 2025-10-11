# tests/test_nsint.py

import json

import pytest
from pydantic import BaseModel, ValidationError


class TestNsIntDetection:
    def test_detects_timestamp_ns(self):
        from lithify.nsint_mapper import detect_ns_fields

        schema = {
            "properties": {
                "timestamp_ns": {"type": "string", "pattern": "^[0-9]+$"},
                "ingest_ns": {"type": "string", "pattern": "^[0-9]+$"},
            }
        }
        fields = detect_ns_fields(schema)
        assert len(fields) == 2
        assert ("timestamp_ns", "#/properties/timestamp_ns") in fields
        assert ("ingest_ns", "#/properties/ingest_ns") in fields

    def test_ignores_non_ns_suffix(self):
        from lithify.nsint_mapper import detect_ns_fields

        schema = {"properties": {"timestamp": {"type": "string", "pattern": "^[0-9]+$"}}}
        fields = detect_ns_fields(schema)
        assert len(fields) == 0

    def test_ignores_wrong_pattern(self):
        from lithify.nsint_mapper import detect_ns_fields

        schema = {"properties": {"timestamp_ns": {"type": "string", "pattern": "^[0-9a-f]+$"}}}
        fields = detect_ns_fields(schema)
        assert len(fields) == 0

    def test_ignores_wrong_type(self):
        from lithify.nsint_mapper import detect_ns_fields

        schema = {"properties": {"timestamp_ns": {"type": "integer"}}}
        fields = detect_ns_fields(schema)
        assert len(fields) == 0


class TestNsIntGeneration:
    def test_generates_complete_code(self):
        from lithify.nsint_generator import generate_nsint_code

        code = generate_nsint_code()

        assert "def _ns_from_json(v):" in code
        assert "BeforeValidator" in code
        assert "PlainSerializer" in code
        assert "NsInt = Annotated[" in code
        assert 'when_used="json"' in code

    def test_generated_code_is_valid_python(self):
        from lithify.nsint_generator import generate_nsint_code

        code = generate_nsint_code()
        full_code = "from typing import Annotated\n" "from pydantic import BeforeValidator, PlainSerializer\n" + code

        compile(full_code, "<string>", "exec")


class TestNsIntValidation:
    def test_generated_nsint_accepts_decimal_string(self):
        from typing import Annotated

        from pydantic import BeforeValidator, PlainSerializer

        from lithify.nsint_generator import generate_nsint_code

        ns = {
            "Annotated": Annotated,
            "BeforeValidator": BeforeValidator,
            "PlainSerializer": PlainSerializer,
        }
        exec(generate_nsint_code(), ns)
        NsInt = ns["NsInt"]

        class Event(BaseModel):
            timestamp_ns: NsInt

        event = Event(timestamp_ns="1762034567890123456")
        assert isinstance(event.timestamp_ns, int)
        assert event.timestamp_ns == 1762034567890123456

    def test_generated_nsint_accepts_int_input(self):
        from typing import Annotated

        from pydantic import BeforeValidator, PlainSerializer

        from lithify.nsint_generator import generate_nsint_code

        ns = {
            "Annotated": Annotated,
            "BeforeValidator": BeforeValidator,
            "PlainSerializer": PlainSerializer,
        }
        exec(generate_nsint_code(), ns)
        NsInt = ns["NsInt"]

        class Event(BaseModel):
            timestamp_ns: NsInt

        event = Event(timestamp_ns=1762034567890123456)
        assert isinstance(event.timestamp_ns, int)

    def test_generated_nsint_serializes_as_string(self):
        from typing import Annotated

        from pydantic import BeforeValidator, PlainSerializer

        from lithify.nsint_generator import generate_nsint_code

        ns = {
            "Annotated": Annotated,
            "BeforeValidator": BeforeValidator,
            "PlainSerializer": PlainSerializer,
        }
        exec(generate_nsint_code(), ns)
        NsInt = ns["NsInt"]

        class Event(BaseModel):
            timestamp_ns: NsInt

        event = Event(timestamp_ns=1762034567890123456)
        json_data = json.loads(event.model_dump_json())
        assert isinstance(json_data["timestamp_ns"], str)
        assert json_data["timestamp_ns"] == "1762034567890123456"

    def test_generated_nsint_rejects_boolean(self):
        from typing import Annotated

        from pydantic import BaseModel, BeforeValidator, PlainSerializer

        from lithify.nsint_generator import generate_nsint_code

        ns = {
            "Annotated": Annotated,
            "BeforeValidator": BeforeValidator,
            "PlainSerializer": PlainSerializer,
        }
        exec(generate_nsint_code(), ns)
        NsInt = ns["NsInt"]

        class Event(BaseModel):
            timestamp_ns: NsInt

        with pytest.raises(ValidationError):
            Event(timestamp_ns=True)

    def test_generated_nsint_rejects_non_decimal_string(self):
        from typing import Annotated

        from pydantic import BaseModel, BeforeValidator, PlainSerializer

        from lithify.nsint_generator import generate_nsint_code

        ns = {
            "Annotated": Annotated,
            "BeforeValidator": BeforeValidator,
            "PlainSerializer": PlainSerializer,
        }
        exec(generate_nsint_code(), ns)
        NsInt = ns["NsInt"]

        class Event(BaseModel):
            timestamp_ns: NsInt

        with pytest.raises(ValidationError):
            Event(timestamp_ns="abc123")
