"""Sample webhook payloads for each form plugin variant."""

WPFORMS_NESTED = {
    "form_id": 42,
    "entry_id": 123,
    "fields": {
        "name":    {"value": "Jane Doe"},
        "email":   {"value": "jane@example.com"},
        "phone":   {"value": "+1 555 0100"},
        "message": {"value": "Hello, I'd like to learn more."},
        "company": {"value": "Example Co"},
    },
}

WPFORMS_FLAT = {
    "form_id": 42,
    "fields": {
        "first_name": "Jane",
        "last_name": "Doe",
        "email": "jane@example.com",
        "phone": "+1 555 0100",
        "message": "Hello, I'd like to learn more.",
        "company": "Example Co",
    },
}

GRAVITY_MAPPED = {
    "form_id": 7,
    "entry": {
        "1.3": "Jane",
        "1.6": "Doe",
        "2":   "jane@example.com",
        "3":   "+1 555 0100",
        "4":   "Hello, I'd like to learn more.",
        "5":   "Example Co",
    },
    "fieldMap": {
        "1.3": "first_name",
        "1.6": "last_name",
        "2":   "email",
        "3":   "phone",
        "4":   "message",
        "5":   "company",
    },
}

FLUENT = {
    "form_id": 12,
    "submission_id": 555,
    "data": {
        "names": {"first_name": "Jane", "last_name": "Doe"},
        "email": "jane@example.com",
        "phone": "+1 555 0100",
        "message": "Hello, I'd like to learn more.",
        "company": "Example Co",
    },
}

ALL_VARIANTS = {
    "wpforms_nested": WPFORMS_NESTED,
    "wpforms_flat": WPFORMS_FLAT,
    "gravity": GRAVITY_MAPPED,
    "fluent": FLUENT,
}
