# CloudGuard Dashboard (Stage 1)

Read-only FastAPI backend that lists registered repositories and recent GitHub Actions workflow runs, attaching CloudGuard scan results from the `cloudguard-scan-reports` artifact.

## Setup

Python 3.13.

```bash
python -m venv .venv
```

Windows:

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

macOS / Linux:

```bash
source .venv/bin/activate
pip install -r requirements.txt
```

## Create a GitHub token

The backend reads **only** `GITHUB_TOKEN` from the environment. It never logs or returns the token.

1. GitHub → **Settings** → **Developer settings** → **Personal access tokens**.
2. Fine-grained token (recommended):
   - Resource owner and repository access for every repo listed in `registry.json`.
   - Repository permissions: **Actions: Read**. For private repositories also **Contents: Read**.
3. Classic token (alternative): `public_repo` for public repositories, or `repo` for private repositories. Actions read is included with `repo`.
4. Copy the token once and store it locally (for example in `.env`, which is gitignored). Do not commit it.

Windows:

```powershell
$env:GITHUB_TOKEN = "github_pat_..."
```

macOS / Linux:

```bash
export GITHUB_TOKEN=github_pat_...
```

## Run

From the project root:

```bash
uvicorn app.main:app --reload
```

- Health: `GET http://127.0.0.1:8000/api/health`
- Registry: `GET http://127.0.0.1:8000/api/repos`
- Runs: `GET http://127.0.0.1:8000/api/repos/avniijain/demo-repo/runs?limit=10`

## Add a repository to `registry.json`

`registry.json` is a JSON list of objects. Each object must include:

| Field | Example | Notes |
| --- | --- | --- |
| `repo` | `owner/name` | Must match `^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$` |
| `workflow` | `security.yml` | Workflow filename used to list runs |
| `container_app` | `ca-cloudguard-demo` | Azure Container App name (metadata) |
| `resource_group` | `rg-cloudguard-demo` | Azure resource group (metadata) |
| `image_repository` | `demo-repo` | Image repository name (metadata) |

Example entry:

```json
{
  "repo": "avniijain/demo-repo",
  "workflow": "security.yml",
  "container_app": "ca-cloudguard-demo",
  "resource_group": "rg-cloudguard-demo",
  "image_repository": "demo-repo"
}
```

The process exits on startup if the file is not valid JSON, is not a list, is missing fields, or has an invalid `repo`.

Only repositories in this file can be queried. There are no write endpoints.

## Tests

```bash
pip install -r requirements-dev.txt
pytest
```

## Deploying

The `security` workflow builds and scans the image on pushes and pull requests. A push to `main` also publishes the image; deployment is enabled when the `ENABLE_DEPLOY` repository variable is set to `true`.

Configure these repository variables for the reusable CloudGuard workflows and Azure Container Apps deployment:

| Variable | Purpose |
| --- | --- |
| `RISK_GATE_MODE` | CloudGuard gate mode; defaults to `enforce`. |
| `SONAR_ORGANIZATION` | Sonar organization used by the scan workflow. |
| `ENABLE_DEPLOY` | Set to `true` to deploy after publishing. |
| `ACR_LOGIN_SERVER` | Azure Container Registry login server. |
| `AZURE_CLIENT_ID` | Entra application or managed identity client ID for workflow OIDC. |
| `AZURE_TENANT_ID` | Entra tenant ID for workflow OIDC. |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription used by the workflow and dashboard. |
| `AZURE_RESOURCE_GROUP` | Resource group containing the Container App. |
| `AZURE_CONTAINER_APP` | Container App name to deploy. |

Add the `SONAR_TOKEN` and `SONAR_HOST_URL` repository secrets for Sonar analysis. In the Azure Container App, configure the `github-token` secret with a GitHub token that has Actions read access to the repositories in `registry.json`. The workflow injects it as `GITHUB_TOKEN` using the Container Apps secret reference `secretref:github-token`.
