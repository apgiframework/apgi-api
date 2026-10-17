"""
Version Information Routes

Provides API versioning information including current version,
supported versions, and deprecation notices.
"""

import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from app import __version__

router = APIRouter(prefix="/v1", tags=["Version"])


# Version configuration - use environment variables with defaults
CURRENT_VERSION = os.getenv("API_VERSION", __version__)
API_VERSION = os.getenv("API_VERSION_PREFIX", "v1")
SUPPORTED_VERSIONS = [API_VERSION]
DEPRECATED_VERSIONS: List[str] = []
DEPRECATED_ENDPOINTS: Dict[str, Dict[str, str]] = {}


@router.get("/version")
async def get_version_info() -> JSONResponse:
    """
    Get API version information.

    Returns current version, supported versions, deprecated versions,
    and links to API documentation.

    **Validates: Requirements 6.1, 6.4**
    """
    return JSONResponse(
        status_code=200,
        content={
            "current_version": CURRENT_VERSION,
            "api_version": API_VERSION,
            "supported_versions": SUPPORTED_VERSIONS,
            "deprecated_versions": DEPRECATED_VERSIONS,
            "api_spec_url": "/openapi.json",
            "documentation_url": "/docs",
            "timestamp": datetime.now(timezone.utc).isoformat() + "Z",
        },
    )


@router.get("/client-docs")
async def get_client_documentation(language: str = "python") -> Any:
    """
    Generate client SDK documentation and examples.

    Args:
        language: Programming language for examples (python, javascript, curl, go, etc.)

    Returns:
        Client library documentation and code examples
    """
    base_url = os.getenv("API_BASE_URL", "http://localhost:8000")

    # Validate base_url to prevent injection or misconfiguration
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        base_url = "http://localhost:8000"

    examples = {
        "python": f"""
# Python client example using requests
import requests

class APGIClient:
    def __init__(self, base_url="{base_url}", token=None):
        self.base_url = base_url.rstrip('/')
        self.session = requests.Session()
        if token:
            self.session.headers.update({{"Authorization": f"Bearer {{token}}"}})

    def login(self, username, password):
        response = self.session.post(
            f"{{self.base_url}}/v1/auth/login",
            json={{"username": username, "password": password}}
        )
        return response.json()

    def get_health(self):
        response = self.session.get(f"{{self.base_url}}/health")
        return response.json()

    def list_tasks(self):
        response = self.session.get(f"{{self.base_url}}/v1/tasks")
        return response.json()

# Usage
client = APGIClient()
health = client.get_health()
print("API Health:", health)
""",
        "javascript": f"""
// JavaScript client example using fetch
class APGIClient {{
    constructor(baseURL = "{base_url}", token = null) {{
        this.baseURL = baseURL.replace(/\\/$/, '');
        this.token = token;
    }}

    async login(username, password) {{
        const response = await fetch(`${{this.baseURL}}/v1/auth/login`, {{
            method: 'POST',
            headers: {{
                'Content-Type': 'application/json',
            }},
            body: JSON.stringify({{ username, password }}),
        }});
        return response.json();
    }}

    async getHealth() {{
        const headers = {{}};
        if (this.token) {{
            headers['Authorization'] = `Bearer ${{this.token}}`;
        }}
        const response = await fetch(`${{this.baseURL}}/health`, {{ headers }});
        return response.json();
    }}

    async listTasks() {{
        const headers = {{}};
        if (this.token) {{
            headers['Authorization'] = `Bearer ${{this.token}}`;
        }}
        const response = await fetch(`${{this.baseURL}}/v1/tasks`, {{ headers }});
        return response.json();
    }}
}}

// Usage
const client = new APGIClient();
const health = await client.getHealth();
console.log('API Health:', health);
""",
        "curl": f"""
# cURL examples

# Health check
curl -X GET "{base_url}/health"

# Login
curl -X POST "{base_url}/v1/auth/login" \\
  -H "Content-Type: application/json" \\
  -d '{{"username": "user@example.com", "password": "password"}}'

# Get access token from login response and use it
TOKEN="your_access_token_here"

# List tasks (requires authentication)
curl -X GET "{base_url}/v1/tasks" \\
  -H "Authorization: Bearer $TOKEN"

# Create session (requires authentication)
curl -X POST "{base_url}/v1/sessions" \\
  -H "Authorization: Bearer $TOKEN" \\
  -H "Content-Type: application/json" \\
  -d '{{"description": "Test session"}}'
""",
        "go": f"""
// Go client example
package main

import (
    "bytes"
    "encoding/json"
    "fmt"
    "io"
    "net/http"
)

type APGIClient struct {{
    BaseURL string
    Token   string
    client  *http.Client
}}

func NewAPGIClient(baseURL, token string) *APGIClient {{
    return &APGIClient{{
        BaseURL: baseURL,
        Token:   token,
        client:  &http.Client{{}},
    }}
}}

func (c *APGIClient) Login(username, password string) (map[string]interface{{}}, error) {{
    loginData := map[string]string{{
        "username": username,
        "password": password,
    }}
    jsonData, _ := json.Marshal(loginData)

    resp, err := c.client.Post(c.BaseURL+"/v1/auth/login", "application/json", bytes.NewBuffer(jsonData))
    if err != nil {{
        return nil, err
    }}
    defer resp.Body.Close()

    var result map[string]interface{{}}
    json.NewDecoder(resp.Body).Decode(&result)
    return result, nil
}}

func (c *APGIClient) GetHealth() (map[string]interface{{}}, error) {{
    req, _ := http.NewRequest("GET", c.BaseURL+"/health", nil)
    if c.Token != "" {{
        req.Header.Set("Authorization", "Bearer "+c.Token)
    }}

    resp, err := c.client.Do(req)
    if err != nil {{
        return nil, err
    }}
    defer resp.Body.Close()

    var result map[string]interface{{}}
    json.NewDecoder(resp.Body).Decode(&result)
    return result, nil
}}

func main() {{
    client := NewAPGIClient("{base_url}", "")
    health, err := client.GetHealth()
    if err != nil {{
        fmt.Println("Error:", err)
        return
    }}
    fmt.Println("API Health:", health)
}}
""",
    }

    supported_languages = list(examples.keys())

    if language not in supported_languages:
        return JSONResponse(
            status_code=400,
            content={
                "error": f"Unsupported language: {language}",
                "supported_languages": supported_languages,
            },
        )

    return {
        "language": language,
        "base_url": base_url,
        "api_version": API_VERSION,
        "documentation": {
            "openapi_spec": "/openapi.json",
            "interactive_docs": "/docs",
            "client_examples": examples[language].strip(),
        },
        "endpoints": {
            "health": "/health",
            "auth_login": "/v1/auth/login",
            "auth_refresh": "/v1/auth/refresh",
            "tasks": "/v1/tasks",
            "sessions": "/v1/sessions",
            "dashboard": "/v1/dashboard",
        },
    }


def configure_deprecated_endpoints(deprecated_config: Dict[str, Dict[str, str]]) -> None:
    """
    Configure deprecated endpoints.

    Args:
        deprecated_config: Dictionary mapping endpoint paths to deprecation info
                          Format: {"/v1/endpoint": {"sunset": "2026-01-01", "replacement": "/v2/endpoint"}}
    """
    DEPRECATED_ENDPOINTS.clear()
    DEPRECATED_ENDPOINTS.update(deprecated_config)


def is_endpoint_deprecated(path: str) -> Optional[Dict[str, str]]:
    """
    Check if an endpoint is deprecated.

    Args:
        path: The endpoint path to check

    Returns:
        Deprecation info if deprecated, None otherwise
    """
    return DEPRECATED_ENDPOINTS.get(path)


def get_deprecated_endpoints() -> Dict[str, Dict[str, str]]:
    """
    Get all deprecated endpoints configuration.

    Returns:
        Dictionary of deprecated endpoints and their info
    """
    return DEPRECATED_ENDPOINTS
