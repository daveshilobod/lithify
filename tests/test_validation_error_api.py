# tests/test_validation_error_api.py
from pydantic import ValidationError
from pydantic_core import InitErrorDetails


def test_from_exception_data_signature():
    errors = [
        InitErrorDetails(
            type="extra_forbidden",
            loc=("__root__", "bad_field"),
            input="value",
        )
    ]

    exc = ValidationError.from_exception_data("TestValidation", errors, input_type="python")

    assert exc.title == "TestValidation"
    assert len(exc.errors()) == 1

    error = exc.errors()[0]
    assert error["type"] == "extra_forbidden"
    assert error["loc"] == ("__root__", "bad_field")
    assert "Extra inputs" in error["msg"]


def test_error_dict_structure():
    error = InitErrorDetails(
        type="value_error",
        loc=("field",),
        input="bad_value",
        ctx={"error": "Value does not match pattern"},
    )

    assert error["type"] == "value_error"
    assert error["loc"] == ("field",)
    assert error["input"] == "bad_value"
    assert error["ctx"]["error"] == "Value does not match pattern"


def test_input_type_parameter():
    errors = [
        InitErrorDetails(
            type="missing",
            loc=("field",),
            input={"incomplete": "data"},
        )
    ]

    exc_python = ValidationError.from_exception_data("Test", errors, input_type="python")
    exc_json = ValidationError.from_exception_data("Test", errors, input_type="json")

    assert len(exc_python.errors()) == 1
    assert len(exc_json.errors()) == 1


def test_location_tuple_structure():
    errors1 = [
        InitErrorDetails(
            type="missing",
            loc=("field",),
            input=None,
        )
    ]
    exc1 = ValidationError.from_exception_data("Test", errors1, input_type="python")
    assert exc1.errors()[0]["loc"] == ("field",)

    errors2 = [
        InitErrorDetails(
            type="missing",
            loc=("parent", "child", 0),
            input=None,
        )
    ]
    exc2 = ValidationError.from_exception_data("Test", errors2, input_type="python")
    assert exc2.errors()[0]["loc"] == ("parent", "child", 0)


def test_multiple_errors():
    errors = [
        InitErrorDetails(type="missing", loc=("field1",), input={"data": "incomplete"}),
        InitErrorDetails(type="missing", loc=("field2",), input={"data": "incomplete"}),
        InitErrorDetails(type="missing", loc=("field3",), input={"data": "incomplete"}),
    ]

    exc = ValidationError.from_exception_data("Test", errors, input_type="python")

    assert len(exc.errors()) == 3
    assert exc.errors()[0]["loc"] == ("field1",)
    assert exc.errors()[1]["loc"] == ("field2",)
    assert exc.errors()[2]["loc"] == ("field3",)


def test_context_dict_optional():
    errors_no_ctx = [
        InitErrorDetails(
            type="missing",
            loc=("field",),
            input=None,
        )
    ]
    exc = ValidationError.from_exception_data("Test", errors_no_ctx, input_type="python")
    assert len(exc.errors()) == 1

    errors_with_ctx = [
        InitErrorDetails(
            type="value_error",
            loc=("field",),
            input=None,
            ctx={"error": "extra info"},
        )
    ]
    exc2 = ValidationError.from_exception_data("Test", errors_with_ctx, input_type="python")
    assert len(exc2.errors()) == 1
    assert "extra info" in exc2.errors()[0]["msg"]


def test_value_error_requires_error_in_ctx():
    errors = [
        InitErrorDetails(
            type="value_error",
            loc=("field",),
            input=123,
            ctx={"error": "This is the required error message"},
        )
    ]

    exc = ValidationError.from_exception_data("Test", errors, input_type="python")
    assert len(exc.errors()) == 1
    assert exc.errors()[0]["type"] == "value_error"
    assert "required error message" in exc.errors()[0]["msg"]
