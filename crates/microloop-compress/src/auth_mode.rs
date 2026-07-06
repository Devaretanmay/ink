
use http::HeaderMap;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum AuthMode {
    Payg,
    OAuth,
    Subscription,
}

impl AuthMode {
    pub fn as_str(self) -> &'static str {
        match self {
            AuthMode::Payg => "payg",
            AuthMode::OAuth => "oauth",
            AuthMode::Subscription => "subscription",
        }
    }
}

const SUBSCRIPTION_UA_PREFIXES: &[&str] = &[
    "claude-cli/",
    "claude-code/",
    "codex-cli/",
    "cursor/",
    "claude-vscode/",
    "github-copilot/",
    "anthropic-cli/",
    "antigravity/",
];

pub fn classify(headers: &HeaderMap) -> AuthMode {
    let ua_owned = match headers.get("user-agent") {
        Some(value) => match value.to_str() {
            Ok(s) => s.to_ascii_lowercase(),
            Err(_) => {
                tracing::warn!(
                    event = "auth_mode_classify_unparseable_user_agent",
                    "non-UTF-8 user-agent header; falling through to bearer-token classification"
                );
                String::new()
            }
        },
        None => String::new(),
    };
    if SUBSCRIPTION_UA_PREFIXES
        .iter()
        .any(|prefix| ua_owned.contains(prefix))
    {
        return AuthMode::Subscription;
    }

    let auth = match headers.get("authorization") {
        Some(value) => match value.to_str() {
            Ok(s) => s,
            Err(_) => {
                tracing::warn!(
                    event = "auth_mode_classify_unparseable_authorization",
                    "non-UTF-8 authorization header; falling back to default Payg"
                );
                ""
            }
        },
        None => "",
    };

    if let Some(token) = auth.strip_prefix("Bearer ") {
        if token.starts_with("sk-ant-oat") {
            return AuthMode::OAuth;
        }
        if token.starts_with("sk-ant-api") || token.starts_with("sk-") {
            return AuthMode::Payg;
        }
        if token.split('.').count() >= 3 {
            return AuthMode::OAuth;
        }
    } else if !auth.is_empty() {
        return AuthMode::OAuth;
    }

    if headers.contains_key("x-api-key") {
        return AuthMode::Payg;
    }
    if headers.contains_key("x-goog-api-key") {
        return AuthMode::Payg;
    }

    AuthMode::Payg
}

#[cfg(test)]
mod inline_tests {

    use super::*;
    use http::HeaderValue;

    #[test]
    fn enum_as_str_is_stable() {
        assert_eq!(AuthMode::Payg.as_str(), "payg");
        assert_eq!(AuthMode::OAuth.as_str(), "oauth");
        assert_eq!(AuthMode::Subscription.as_str(), "subscription");
    }

    #[test]
    fn empty_headers_default_to_payg() {
        let headers = HeaderMap::new();
        assert_eq!(classify(&headers), AuthMode::Payg);
    }

    #[test]
    fn unparseable_auth_falls_back_to_default() {
        let mut headers = HeaderMap::new();
        headers.insert(
            "authorization",
            HeaderValue::from_bytes(b"\xFFnope").unwrap(),
        );
        assert_eq!(classify(&headers), AuthMode::Payg);
    }
}
