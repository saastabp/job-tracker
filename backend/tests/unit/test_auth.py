from common.auth import claims, user_email, user_sub


def test_extracts_claims_from_event():
    event = {
        "requestContext": {
            "authorizer": {
                "jwt": {"claims": {"sub": "abc", "email": "x@y.z"}}
            }
        }
    }
    assert claims(event) == {"sub": "abc", "email": "x@y.z"}
    assert user_sub(event) == "abc"
    assert user_email(event) == "x@y.z"


def test_missing_claims_returns_none():
    assert user_sub({}) is None
    assert user_email({"requestContext": {}}) is None