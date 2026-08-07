use std::time::Duration;

use futures_util::future::join;
use semver::Version;
use serde::{Deserialize, Serialize};
use tauri::{Manager, ResourceId, Runtime, Webview};
use tauri_plugin_updater::{Update, UpdaterExt};
use url::Url;

const GITHUB_UPDATE_ENDPOINT: &str =
    "https://github.com/alikon-art/DeterminFlow/releases/latest/download/latest.json";
const GITEE_LATEST_RELEASE_API: &str =
    "https://gitee.com/api/v5/repos/alikon/DeterminFlow/releases/latest";
const UPDATE_TIMEOUT: Duration = Duration::from_secs(15);

#[derive(Deserialize)]
struct GiteeAsset {
    name: String,
    browser_download_url: String,
}

#[derive(Deserialize)]
struct GiteeRelease {
    assets: Vec<GiteeAsset>,
}

struct SelectedUpdates {
    primary: Update,
    fallback: Option<Update>,
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
enum UpdateSource {
    Github,
    Gitee,
}

#[derive(Debug, Eq, PartialEq)]
struct UpdatePlan {
    primary: UpdateSource,
    fallback: Option<UpdateSource>,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct UpdateMetadata {
    rid: ResourceId,
    #[serde(skip_serializing_if = "Option::is_none")]
    fallback_rid: Option<ResourceId>,
    current_version: String,
    version: String,
    date: Option<String>,
    body: Option<String>,
    raw_json: serde_json::Value,
}

async fn gitee_update_endpoint() -> Result<Url, String> {
    let client = reqwest::Client::builder()
        .timeout(UPDATE_TIMEOUT)
        .build()
        .map_err(|error| error.to_string())?;
    let release = client
        .get(GITEE_LATEST_RELEASE_API)
        .header(reqwest::header::USER_AGENT, "DeterminFlow-Updater")
        .send()
        .await
        .map_err(|error| error.to_string())?
        .error_for_status()
        .map_err(|error| error.to_string())?
        .json::<GiteeRelease>()
        .await
        .map_err(|error| error.to_string())?;
    let asset = release
        .assets
        .into_iter()
        .find(|asset| asset.name == "latest.json")
        .ok_or_else(|| "Gitee 最新发行版缺少 latest.json".to_string())?;
    Url::parse(&asset.browser_download_url).map_err(|error| error.to_string())
}

async fn check_endpoint<R: Runtime>(
    webview: Webview<R>,
    endpoint: Result<Url, String>,
) -> Result<Option<Update>, String> {
    let endpoint = endpoint?;
    if endpoint.scheme() != "https" {
        return Err("更新地址必须使用 HTTPS".to_string());
    }
    let updater = webview
        .updater_builder()
        .endpoints(vec![endpoint])
        .map_err(|error| error.to_string())?
        .timeout(UPDATE_TIMEOUT)
        .build()
        .map_err(|error| error.to_string())?;
    updater.check().await.map_err(|error| error.to_string())
}

fn plan_update_sources(
    github: Option<(&str, &str)>,
    gitee: Option<(&str, &str)>,
) -> Result<Option<UpdatePlan>, String> {
    let (github, gitee) = match (github, gitee) {
        (None, None) => return Ok(None),
        (Some(_), None) => {
            return Ok(Some(UpdatePlan {
                primary: UpdateSource::Github,
                fallback: None,
            }));
        }
        (None, Some(_)) => {
            return Ok(Some(UpdatePlan {
                primary: UpdateSource::Gitee,
                fallback: None,
            }));
        }
        (Some(github), Some(gitee)) => (github, gitee),
    };

    let github_version = Version::parse(github.0).map_err(|error| error.to_string())?;
    let gitee_version = Version::parse(gitee.0).map_err(|error| error.to_string())?;
    if github_version != gitee_version {
        return Ok(Some(UpdatePlan {
            primary: if github_version > gitee_version {
                UpdateSource::Github
            } else {
                UpdateSource::Gitee
            },
            fallback: None,
        }));
    }

    if !gitee.1.is_empty() && github.1 == gitee.1 {
        return Ok(Some(UpdatePlan {
            primary: UpdateSource::Gitee,
            fallback: Some(UpdateSource::Github),
        }));
    }

    Ok(Some(UpdatePlan {
        primary: UpdateSource::Github,
        fallback: None,
    }))
}

fn choose_update(
    github: Result<Option<Update>, String>,
    gitee: Result<Option<Update>, String>,
) -> Result<Option<SelectedUpdates>, String> {
    let (mut github, mut gitee) = match (github, gitee) {
        (Ok(github), Ok(gitee)) => (github, gitee),
        (Ok(available), Err(_)) => {
            return Ok(available.map(|primary| SelectedUpdates {
                primary,
                fallback: None,
            }));
        }
        (Err(_), Ok(available)) => {
            return Ok(available.map(|primary| SelectedUpdates {
                primary,
                fallback: None,
            }));
        }
        (Err(github_error), Err(gitee_error)) => {
            return Err(format!(
                "GitHub 与 Gitee 更新源均不可用: {github_error}; {gitee_error}"
            ));
        }
    };

    let plan = plan_update_sources(
        github
            .as_ref()
            .map(|update| (update.version.as_str(), update.signature.as_str())),
        gitee
            .as_ref()
            .map(|update| (update.version.as_str(), update.signature.as_str())),
    )?;
    Ok(plan.map(|plan| match plan.primary {
        UpdateSource::Github => SelectedUpdates {
            primary: github
                .take()
                .expect("GitHub update plan requires an update"),
            fallback: match plan.fallback {
                Some(UpdateSource::Gitee) => gitee.take(),
                _ => None,
            },
        },
        UpdateSource::Gitee => SelectedUpdates {
            primary: gitee.take().expect("Gitee update plan requires an update"),
            fallback: match plan.fallback {
                Some(UpdateSource::Github) => github.take(),
                _ => None,
            },
        },
    }))
}

#[cfg(test)]
mod tests {
    use super::{plan_update_sources, UpdatePlan, UpdateSource};

    #[test]
    fn gitee_is_primary_with_github_fallback_for_the_same_signed_release() {
        let plan = plan_update_sources(
            Some(("1.2.3", "same-signature")),
            Some(("1.2.3", "same-signature")),
        )
        .unwrap();

        assert_eq!(
            plan,
            Some(UpdatePlan {
                primary: UpdateSource::Gitee,
                fallback: Some(UpdateSource::Github),
            })
        );
    }

    #[test]
    fn newer_release_wins_without_cross_version_fallback() {
        let plan = plan_update_sources(
            Some(("1.2.4", "github-signature")),
            Some(("1.2.3", "gitee-signature")),
        )
        .unwrap();

        assert_eq!(
            plan,
            Some(UpdatePlan {
                primary: UpdateSource::Github,
                fallback: None,
            })
        );
    }

    #[test]
    fn signature_mismatch_falls_back_to_the_authoritative_github_release() {
        let plan = plan_update_sources(
            Some(("1.2.3", "github-signature")),
            Some(("1.2.3", "gitee-signature")),
        )
        .unwrap();

        assert_eq!(
            plan,
            Some(UpdatePlan {
                primary: UpdateSource::Github,
                fallback: None,
            })
        );
    }
}

#[tauri::command]
pub async fn check_update_sources<R: Runtime>(
    webview: Webview<R>,
) -> Result<Option<UpdateMetadata>, String> {
    let github_url = Url::parse(GITHUB_UPDATE_ENDPOINT).map_err(|error| error.to_string());
    let github = check_endpoint(webview.clone(), github_url);

    let gitee = async {
        let endpoint = gitee_update_endpoint().await;
        check_endpoint(webview.clone(), endpoint).await
    };
    let (github_result, gitee_result) = join(github, gitee).await;
    let selected = choose_update(github_result, gitee_result)?;

    Ok(selected.map(|selected| {
        let fallback_rid = selected
            .fallback
            .map(|update| webview.resources_table().add(update));
        let update = selected.primary;
        UpdateMetadata {
            current_version: update.current_version.clone(),
            version: update.version.clone(),
            date: update.date.map(|date| date.to_string()),
            body: update.body.clone(),
            raw_json: update.raw_json.clone(),
            rid: webview.resources_table().add(update),
            fallback_rid,
        }
    }))
}
