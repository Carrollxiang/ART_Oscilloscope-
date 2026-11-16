# Adapted from RTMQ documentation governance; see doc/adr/0001-document-governance.md.
[CmdletBinding()]
param(
    [string]$RepositoryRoot
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RepositoryRoot)) {
    $scriptDirectory = Split-Path -Parent $MyInvocation.MyCommand.Path
    $RepositoryRoot = Split-Path -Parent $scriptDirectory
}

$resolvedRepository = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$documentationRoot = Join-Path $resolvedRepository 'doc'
$documentationIndex = Join-Path $documentationRoot 'README.md'
$allowedRootDocuments = @('README.md', 'AGENTS.md', 'CONTEXT.md')
$requiredRootDocuments = @('README.md', 'AGENTS.md')
$runtimeRootAssets = @('requirements.txt', 'requirements-dev.txt')
$rootDocumentExtensions = @('.md', '.txt', '.pdf', '.html', '.rst')

if (-not (Test-Path -LiteralPath $documentationRoot -PathType Container)) {
    throw "Documentation directory not found: $documentationRoot"
}

$validKinds = @('policy', 'decision', 'reference', 'procedure', 'record', 'index')
$validStatuses = @('draft', 'proposed', 'active', 'deprecated', 'superseded', 'archived')
$validAuthorities = @('canonical', 'supporting', 'evidence')
$requiredFields = @('kind', 'status', 'scope', 'authority', 'owner')
$errors = [System.Collections.Generic.List[string]]::new()
$canonicalDocuments = @{}
$markdownFiles = @(Get-ChildItem -LiteralPath $documentationRoot -Recurse -File -Filter '*.md')
foreach ($rootMarkdown in @('README.md', 'CONTEXT.md')) {
    $rootPath = Join-Path $resolvedRepository $rootMarkdown
    if (Test-Path -LiteralPath $rootPath -PathType Leaf) { $markdownFiles += Get-Item -LiteralPath $rootPath }
}
$rootDocuments = @(
    Get-ChildItem -LiteralPath $resolvedRepository -File |
        Where-Object Extension -in $rootDocumentExtensions
)

foreach ($requiredRootDocument in $requiredRootDocuments) {
    if (-not (Test-Path -LiteralPath (Join-Path $resolvedRepository $requiredRootDocument) -PathType Leaf)) {
        $errors.Add("root: missing required control document '$requiredRootDocument'")
    }
}
foreach ($file in $rootDocuments) {
    if ($file.Name -notin $allowedRootDocuments -and $file.Name -notin $runtimeRootAssets) {
        $errors.Add("$($file.Name): documentation file must move under doc/ or be added as an explicit root control document")
    }
}

foreach ($file in $markdownFiles) {
    $relativePath = $file.FullName.Substring($resolvedRepository.Length).TrimStart('\', '/').Replace('\', '/')
    $lines = @(Get-Content -LiteralPath $file.FullName -Encoding UTF8)

    if ($lines.Count -eq 0 -or $lines[0] -ne '---') {
        $errors.Add("${relativePath}: missing YAML front matter")
        continue
    }

    $frontMatterEnd = [Array]::IndexOf($lines, '---', 1)
    if ($frontMatterEnd -lt 1) {
        $errors.Add("${relativePath}: unclosed YAML front matter")
        continue
    }

    $metadata = @{}
    foreach ($line in $lines[1..($frontMatterEnd - 1)]) {
        if ([string]::IsNullOrWhiteSpace($line) -or $line.TrimStart().StartsWith('#')) {
            continue
        }
        if ($line -notmatch '^([a-z_]+):\s*(.*)$') {
            $errors.Add("${relativePath}: unsupported front-matter line '$line'")
            continue
        }
        if ($metadata.ContainsKey($Matches[1])) { $errors.Add("${relativePath}: duplicate metadata key '$($Matches[1])'") }
        $metadata[$Matches[1]] = $Matches[2].Trim().Trim('"')
    }

    foreach ($field in $requiredFields) {
        if (-not $metadata.ContainsKey($field) -or [string]::IsNullOrWhiteSpace($metadata[$field])) {
            $errors.Add("${relativePath}: missing required field '$field'")
        }
    }

    if ($metadata.kind -notin $validKinds) {
        $errors.Add("${relativePath}: invalid kind '$($metadata.kind)'")
    }
    if ($metadata.status -notin $validStatuses) {
        $errors.Add("${relativePath}: invalid status '$($metadata.status)'")
    }
    if ($metadata.authority -notin $validAuthorities) {
        $errors.Add("${relativePath}: invalid authority '$($metadata.authority)'")
    }
    if ($metadata.scope -notmatch '^[a-z0-9]+(?:[.-][a-z0-9]+)*$') {
        $errors.Add("${relativePath}: invalid scope '$($metadata.scope)'")
    }

    if ($metadata.kind -eq 'record' -and $file.Name -notmatch '^\d{4}-\d{2}-\d{2}[_-]') {
        $errors.Add("${relativePath}: record filename must begin with YYYY-MM-DD")
    }
    if ($metadata.status -eq 'archived' -and $metadata.authority -eq 'canonical') {
        $errors.Add("${relativePath}: archive cannot claim canonical authority")
    }
    if ($metadata.status -eq 'deprecated' -and
        (-not $metadata.ContainsKey('replacement') -or [string]::IsNullOrWhiteSpace($metadata.replacement))) {
        $errors.Add("${relativePath}: deprecated documents require 'replacement'")
    }
    if ($metadata.status -eq 'superseded' -and
        (-not $metadata.ContainsKey('superseded_by') -or [string]::IsNullOrWhiteSpace($metadata.superseded_by))) {
        $errors.Add("${relativePath}: superseded documents require 'superseded_by'")
    }

    if ($metadata.status -eq 'active' -and $metadata.authority -eq 'canonical') {
        foreach ($field in @('last_verified', 'applies_to')) {
            if (-not $metadata.ContainsKey($field) -or
                [string]::IsNullOrWhiteSpace($metadata[$field]) -or
                $metadata[$field] -eq 'null') {
                $errors.Add("${relativePath}: active canonical documents require '$field'")
            }
        }

        $parsedDate = [datetime]::MinValue
        if (-not [datetime]::TryParseExact($metadata.last_verified, 'yyyy-MM-dd', [Globalization.CultureInfo]::InvariantCulture, [Globalization.DateTimeStyles]::None, [ref]$parsedDate)) {
            $errors.Add("${relativePath}: last_verified must be a valid YYYY-MM-DD date")
        }
        $canonicalKey = "$($metadata.scope)|$($metadata.applies_to)".ToLowerInvariant()
        if ($canonicalDocuments.ContainsKey($canonicalKey)) {
            $errors.Add(
                "${relativePath}: duplicate active canonical scope/applicability with " +
                $canonicalDocuments[$canonicalKey]
            )
        } else {
            $canonicalDocuments[$canonicalKey] = $relativePath
        }
    }
}

$indexText = Get-Content -Raw -LiteralPath $documentationIndex -Encoding UTF8
$nonMarkdownFiles = @(
    Get-ChildItem -LiteralPath $documentationRoot -Recurse -File |
        Where-Object Extension -ne '.md' |
        Where-Object {
            $relativeToRepository = $_.FullName.Substring($resolvedRepository.Length).TrimStart('\', '/').Replace('\', '/')
            $trackedPath = & git -C $resolvedRepository ls-files --cached -- $relativeToRepository 2>$null
            if (-not [string]::IsNullOrWhiteSpace($trackedPath)) {
                return $true
            }

            & git -C $resolvedRepository check-ignore -q --no-index -- $relativeToRepository
            return $LASTEXITCODE -ne 0
        }
)
foreach ($file in $nonMarkdownFiles) {
    $relativeToDocumentation = $file.FullName.Substring($documentationRoot.Length).TrimStart('\', '/').Replace('\', '/')
    if (-not $indexText.Contains($relativeToDocumentation)) {
        $errors.Add("doc/${relativeToDocumentation}: non-Markdown asset is not registered in doc/README.md")
    }
}

# Validate local Markdown file targets and navigation reachability. Fenced code is not a link.
$linksByFile = @{}
$linkSources = @($markdownFiles) + @(Get-Item -LiteralPath (Join-Path $resolvedRepository 'AGENTS.md'))
foreach ($file in $linkSources) {
    $body = Get-Content -Raw -LiteralPath $file.FullName -Encoding UTF8
    $body = [regex]::Replace($body, '(?ms)^```.*?^```[^\r\n]*', '')
    $targets = [System.Collections.Generic.List[string]]::new()
    foreach ($match in [regex]::Matches($body, '\]\(([^)]+)\)')) {
        $target = $match.Groups[1].Value.Trim()
        if ($target -match '^(?:[a-zA-Z][a-zA-Z0-9+.-]*:|#)') { continue }
        $pathPart = ($target -split '#', 2)[0].Trim('<', '>')
        if ([string]::IsNullOrWhiteSpace($pathPart)) { continue }
        $targetPath = [IO.Path]::GetFullPath((Join-Path $file.DirectoryName ([uri]::UnescapeDataString($pathPart))))
        if (-not (Test-Path -LiteralPath $targetPath)) {
            $errors.Add("$($file.Name): broken local link '$target'")
        } else {
            $targets.Add($targetPath)
        }
    }
    $linksByFile[$file.FullName] = @($targets.ToArray())
}
$visited = @{}
$pending = [System.Collections.Generic.Queue[string]]::new()
$pending.Enqueue($documentationIndex)
while ($pending.Count -gt 0) {
    $path = $pending.Dequeue()
    if ($visited.ContainsKey($path)) { continue }
    $visited[$path] = $true
    foreach ($target in $linksByFile[$path]) { if ($linksByFile.ContainsKey($target)) { $pending.Enqueue($target) } }
}
foreach ($file in $markdownFiles) {
    if (-not $visited.ContainsKey($file.FullName)) { $errors.Add("$($file.Name): unreachable from doc/README.md") }
}

if ($errors.Count -gt 0) {
    Write-Error ("Document governance validation failed:`n- " + ($errors -join "`n- "))
    exit 1
}

Write-Output (
    "Document governance OK: {0} root control documents, {1} governed Markdown files, {2} non-Markdown assets, {3} active canonical scopes." -f
    $requiredRootDocuments.Count,
    $markdownFiles.Count,
    $nonMarkdownFiles.Count,
    $canonicalDocuments.Count
)
