import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { appendFileSync, mkdirSync, writeFileSync } from 'node:fs'
import { basename, dirname, isAbsolute, relative, resolve, sep } from 'node:path'

const CODE_MODE_CONTRACT = Object.freeze({
  formula: 'plan -> sandbox build -> test -> hash -> package',
  loop: 'entry -> preflight -> sandbox -> patch -> test -> exit',
  operators: ['CI/CD', 'PCM', 'MBA']
})

function input(name, fallback = '') {
  return (process.env[`INPUT_${name.toUpperCase()}`] || fallback).trim()
}

function canonical(value) {
  if (Array.isArray(value)) return value.map(canonical)
  if (value !== null && typeof value === 'object') {
    return Object.fromEntries(
      Object.keys(value)
        .sort()
        .map((key) => [key, canonical(value[key])])
    )
  }
  return value
}

function canonicalBytes(value) {
  return Buffer.from(`${JSON.stringify(canonical(value))}\n`, 'utf8')
}

function sha256(value) {
  return createHash('sha256').update(value).digest('hex').toUpperCase()
}

function workspacePath(workspace, requested) {
  if (isAbsolute(requested)) {
    throw new Error('receipt_path must be workspace-relative')
  }
  const target = resolve(workspace, requested)
  if (target !== workspace && !target.startsWith(`${workspace}${sep}`)) {
    throw new Error('receipt_path escapes the governed workspace')
  }
  return target
}

function commandPlan(profile, pythonExecutable, pnpmExecutable) {
  const profiles = {
    'policy-smoke': [
      [
        pythonExecutable,
        ['-m', 'pytest', '-q', 'tests/test_github_automation_governance.py']
      ]
    ],
    'governed-quality': [
      [
        pythonExecutable,
        [
          'plugins/evidence-lane-plugin/scripts/sync_website_plan_projection.py',
          '--check'
        ]
      ],
      [
        pythonExecutable,
        ['scripts/generate_repository_source_fingerprints.py', '--check']
      ],
      [pythonExecutable, ['-m', 'ruff', 'check', '.']],
      [pythonExecutable, ['-m', 'mypy']]
    ],
    'governed-lifecycle': [
      [
        pythonExecutable,
        [
          '-m',
          'pytest',
          '-q',
          'tests/test_acceptance.py',
          'tests/test_backlog_enrollment.py',
          'tests/test_batch_completion.py',
          'tests/test_engine_pv.py',
          'tests/test_host_plan_rehydration.py',
          'tests/test_persistent_step_task_list_contract.py'
        ]
      ],
      [
        pythonExecutable,
        [
          '-m',
          'pytest',
          '-q',
          'tests/test_lifecycle.py',
          'tests/test_reader_query.py',
          'tests/test_runtime_activation.py',
          'tests/test_session_flash.py'
        ]
      ],
      [
        pythonExecutable,
        [
          '-m',
          'pytest',
          '-q',
          'tests/test_security_persistence.py',
          'tests/test_status_history_compatibility.py',
          'tests/test_successor_addendum.py'
        ]
      ]
    ],
    'governed-lanes': [
      [
        pythonExecutable,
        [
          '-m',
          'pytest',
          '-q',
          'tests/test_artifact_contract.py',
          'tests/test_full_app_ui_conformance.py',
          'tests/test_operating_modes.py',
          'tests/test_topology_reconciliation.py',
          'tests/test_topology_rendering.py',
          'tests/test_universal_brain_v060.py',
          'tests/test_universal_lanes.py',
          'tests/test_v070_improvements.py',
          'tests/test_v080_authorities.py'
        ]
      ]
    ],
    'governed-sources-mcp': [
      [
        pythonExecutable,
        [
          '-m',
          'pytest',
          '-q',
          'tests/test_ci_workflow_adapters.py',
          'tests/test_custom_source_schema.py',
          'tests/test_github_automation_governance.py',
          'tests/test_mcp_plugin.py',
          'tests/test_source_authority_registry.py',
          'tests/test_source_git_history.py',
          'tests/test_source_graph.py',
          'tests/test_source_identity.py',
          'tests/test_source_policy.py',
          'tests/test_source_sqlite.py'
        ]
      ]
    ],
    'preview-build': [
      [
        pnpmExecutable,
        [
          '--dir',
          'apps/evidence-lane-app',
          'build'
        ]
      ]
    ]
  }
  if (!Object.hasOwn(profiles, profile)) {
    throw new Error(`unsupported fixed CI profile: ${profile}`)
  }
  return profiles[profile]
}

function spawnGoverned(executable, args, options) {
  if (process.platform !== 'win32' || !executable.endsWith('.cmd')) {
    return spawnSync(executable, args, options)
  }
  const unsafe = [executable, ...args].find((value) => /[\r\n&|<>^%!]/.test(value))
  if (unsafe) {
    throw new Error('Windows command adapter rejected a shell metacharacter')
  }
  return spawnSync(
    process.env.ComSpec || 'cmd.exe',
    ['/d', '/c', executable, ...args],
    options
  )
}

function writeOutput(name, value) {
  const outputPath = process.env.GITHUB_OUTPUT
  if (outputPath) appendFileSync(outputPath, `${name}=${value}\n`, 'utf8')
}

function run() {
  const workspace = resolve(process.env.GITHUB_WORKSPACE || process.cwd())
  const profile = input('profile')
  const pythonExecutable = input('python_executable', 'python')
  const pnpmExecutable = input('pnpm_executable', 'pnpm')
  const receiptRelative = input(
    'receipt_path',
    '.runtime/ci/evidence-lane-ci-receipt.json'
  )
  const receiptPath = workspacePath(workspace, receiptRelative)
  const plan = commandPlan(profile, pythonExecutable, pnpmExecutable)
  const commandResults = []

  console.log(`CODE MODE FORMULA: ${CODE_MODE_CONTRACT.formula}`)
  console.log(`CODE MODE LOOP: ${CODE_MODE_CONTRACT.loop}`)
  console.log(`CODE MODE OPERATORS: ${CODE_MODE_CONTRACT.operators.join(' + ')}`)

  let status = 'PASS'
  for (const [executable, args] of plan) {
    const result = spawnGoverned(executable, args, {
      cwd: workspace,
      encoding: 'utf8',
      env: {
        ...process.env,
        CI: 'true',
        PYTHONHASHSEED: '0',
        TZ: 'UTC'
      },
      timeout: 12 * 60 * 1000
    })
    const stdout = result.stdout || ''
    const stderr = result.stderr || ''
    if (stdout) process.stdout.write(stdout)
    if (stderr) process.stderr.write(stderr)
    const exitCode = result.error ? -1 : (result.status ?? -1)
    commandResults.push({
      args,
      executable: basename(executable),
      exit_code: exitCode,
      execution_error: result.error ? result.error.message : null,
      stderr_sha256: sha256(Buffer.from(stderr, 'utf8')),
      stdout_sha256: sha256(Buffer.from(stdout, 'utf8'))
    })
    if (exitCode !== 0) {
      status = 'BLOCKED'
      break
    }
  }

  const body = {
    schema: 'evidence-lane.code-mode-ci-execution-receipt.v1',
    status,
    profile,
    code_mode_contract: CODE_MODE_CONTRACT,
    command_results: commandResults,
    receipt_path: relative(workspace, receiptPath).split(sep).join('/')
  }
  const receiptSha256 = sha256(canonicalBytes(body))
  const receipt = { ...body, receipt_sha256: receiptSha256 }
  mkdirSync(dirname(receiptPath), { recursive: true })
  writeFileSync(receiptPath, `${JSON.stringify(receipt, null, 2)}\n`, 'utf8')
  writeOutput('status', status)
  writeOutput('receipt_sha256', receiptSha256)
  writeOutput('receipt_path', body.receipt_path)

  const summaryPath = process.env.GITHUB_STEP_SUMMARY
  if (summaryPath) {
    appendFileSync(
      summaryPath,
      [
        '## Evidence Lane Code-mode CI receipt',
        '',
        `- Formula: \`${CODE_MODE_CONTRACT.formula}\``,
        `- Operators: ${CODE_MODE_CONTRACT.operators.join(' + ')}`,
        `- Profile: \`${profile}\``,
        `- Status: **${status}**`,
        `- Receipt SHA-256: \`${receiptSha256}\``,
        ''
      ].join('\n'),
      'utf8'
    )
  }
  if (status !== 'PASS') process.exitCode = 1
}

try {
  run()
} catch (error) {
  console.error(error instanceof Error ? error.message : String(error))
  process.exitCode = 1
}
