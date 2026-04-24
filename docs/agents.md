# Agents

Full catalog of agents shipped by ai-dev-foundry, grouped by domain. The top-level [README](../README.md) has the compact table; this doc has per-agent descriptions and usage examples.

## Planning

| Agent | What it does |
|-------|--------------|
| **tech-lead** | Breaks down work, creates implementation plans, makes technical decisions |
| **systems-architect** | Explains architecture, analyzes change impact, maps data flows |
| **product-owner** | Defines product direction, writes specs, prioritizes ruthlessly |

## Building

| Agent | What it does |
|-------|--------------|
| **senior-dev** | Implements features with production-quality code and tests |
| **junior-dev** | Implements straightforward features and fixes simple bugs; suited for focused, well-scoped tasks |
| **debugger** | Investigates bugs systematically with root cause analysis |
| **refactoring-expert** | Improves code structure without changing behavior |
| **prompt-engineer** | Crafts effective prompts for AI models |
| **agent-specialist** | Designs and optimizes AI agents with strong contracts |

## Quality

| Agent | What it does |
|-------|--------------|
| **code-reviewer** | Reviews code for correctness, security, and maintainability |
| **test-engineer** | Designs comprehensive test suites (unit, integration, e2e) |
| **security-auditor** | Identifies vulnerabilities and recommends mitigations |

## Operations

| Agent | What it does |
|-------|--------------|
| **prod-engineer** | Triages incidents, diagnoses with evidence, hardens systems |
| **pr-refiner** | Processes PR feedback and implements changes with critical thinking |
| **documentation-writer** | Creates minimal, DRY documentation |
| **claudemd-architect** | Creates and updates CLAUDE.md files for agent-ready repos |

## Data & ML

| Agent | What it does |
|-------|--------------|
| **data-engineer** | Designs and reviews ETL/ELT pipelines, data models, orchestration, and data quality strategies |
| **ml-architect** | Designs ML systems end-to-end: data pipelines, training, serving, monitoring |

## Design

| Agent | What it does |
|-------|--------------|
| **ux-designer** | Research-backed UX critique covering usability, accessibility, information architecture, and business alignment |
| **ui-developer** | Pixel-perfect UI implementation with design system thinking, animation performance, and component quality |
| **digital-designer** | Creates print-ready layouts (booklets, brochures, posters) |

## Sales / Solutions

| Agent | What it does |
|-------|--------------|
| **solution-eng** | Runs discovery, designs solutions, manages POCs |
| **marketing-lead** | Crafts positioning, messaging, and go-to-market copy that converts |

## Professional

| Agent | What it does |
|-------|--------------|
| **career-advisor** | Diagnoses market position and produces ROI-ranked actions for role targeting, resume strategy, and career pivots |
| **legal-counsel** | Analyzes legal issues, reviews contracts, and drafts legal work product with strict anti-hallucination and jurisdiction discipline |

## Usage

`@agent-name` is a Claude Code invocation pattern. For Gemini CLI and Codex CLI, agents are selected automatically based on your request; explicit `@agent-name` targeting is not required.

```
@tech-lead plan how to add user notifications
@senior-dev add pagination to the users endpoint
@debugger the API returns 500 on POST /users
@security-auditor review the authentication module
@systems-architect how does caching work in this service?
@code-reviewer check my changes before I open a PR
```
