// Thin repository adapter. The fixed Python runner owns profile selection/results.
import { spawnSync } from 'node:child_process'
import { createHash } from 'node:crypto'
import { appendFileSync, existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'

const profile = process.env.INPUT_PROFILE || ''
if (!/^[a-z][a-z0-9_-]{0,39}$/.test(profile)) throw new Error('Invalid CI profile')
const root = resolve(process.env.GITHUB_WORKSPACE || process.cwd())
if (existsSync(resolve(root, `.work/ci/${profile}`))) throw new Error('CI output already exists')
const result = spawnSync(process.env.INPUT_PYTHON_EXECUTABLE || 'python', [
  '-B', 'scripts/maintainer_ci.py', 'run', '--profile', profile
], { cwd: root, env: process.env, stdio: 'inherit', shell: false, timeout: 35 * 60 * 1000 })
if (result.error) throw result.error
process.exitCode = result.status ?? 1
const receiptPath = `.work/ci/${profile}/receipt.json`
try {
  const bytes = readFileSync(resolve(root, receiptPath))
  const receipt = JSON.parse(bytes)
  if (process.env.GITHUB_OUTPUT) appendFileSync(process.env.GITHUB_OUTPUT, [
    `status=${receipt.status}`,
    `receipt_sha256=${createHash('sha256').update(bytes).digest('hex')}`,
    `receipt_path=${receiptPath}`, ''
  ].join('\n'))
} catch (error) {
  console.error(`The CI result could not be read: ${error.message}`)
  process.exitCode = 1
}
