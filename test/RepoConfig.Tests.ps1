BeforeAll {
    $script:RepoRoot = Resolve-Path (Join-Path $PSScriptRoot '..')
}

Describe 'devcontainer' {
    BeforeAll {
        $script:DevcontainerPath = Join-Path $script:RepoRoot '.devcontainer' 'devcontainer.json'
    }

    It 'exists' {
        Test-Path -LiteralPath $script:DevcontainerPath | Should -Be $true
    }

    It 'is valid JSON with the expected features' {
        $json = Get-Content -LiteralPath $script:DevcontainerPath -Raw | ConvertFrom-Json
        $featureNames = $json.features.PSObject.Properties.Name
        $featureNames | Should -Contain 'ghcr.io/devcontainers/features/node:1'
        $featureNames | Should -Contain 'ghcr.io/devcontainers/features/powershell:1'
        $featureNames | Should -Contain 'ghcr.io/devcontainers/features/github-cli:1'
        $featureNames | Should -Contain 'ghcr.io/devcontainers/features/docker-in-docker:2'
    }

    It 'forwards the three service ports' {
        $json = Get-Content -LiteralPath $script:DevcontainerPath -Raw | ConvertFrom-Json
        $json.forwardPorts | Should -Contain 80
        $json.forwardPorts | Should -Contain 4099
        $json.forwardPorts | Should -Contain 8080
    }
}

Describe 'dependabot' {
    It 'covers uv, github-actions, and docker ecosystems' {
        $content = Get-Content -LiteralPath (Join-Path $script:RepoRoot '.github' 'dependabot.yml') -Raw
        $content | Should -Match 'package-ecosystem:\s*"uv"'
        $content | Should -Match 'package-ecosystem:\s*"github-actions"'
        $content | Should -Match 'package-ecosystem:\s*"docker"'
    }
}

Describe 'pre-commit' {
    It 'references ruff and the secret scanner' {
        $content = Get-Content -LiteralPath (Join-Path $script:RepoRoot '.pre-commit-config.yaml') -Raw
        $content | Should -Match 'ruff-pre-commit'
        $content | Should -Match 'scan-uncommitted-secrets'
    }
}

Describe 'release automation' {
    It 'has both the release workflow and categorization config' {
        Test-Path -LiteralPath (Join-Path $script:RepoRoot '.github' 'workflows' 'release.yml') | Should -Be $true
        Test-Path -LiteralPath (Join-Path $script:RepoRoot '.github' 'release.yml') | Should -Be $true
        $workflow = Get-Content -LiteralPath (Join-Path $script:RepoRoot '.github' 'workflows' 'release.yml') -Raw
        $workflow | Should -Match '--generate-notes'
        $workflow | Should -Match "v\*\.\*\.\*"
    }
}

Describe '.gitignore hygiene' {
    It 'ignores OS cruft and root-level node_modules' {
        $content = Get-Content -LiteralPath (Join-Path $script:RepoRoot '.gitignore') -Raw
        $content | Should -Match '\.DS_Store'
        $content | Should -Match 'Thumbs\.db'
        $content | Should -Match 'node_modules/'
    }
}
