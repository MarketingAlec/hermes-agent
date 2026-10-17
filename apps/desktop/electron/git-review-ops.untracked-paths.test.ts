import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, test } from 'vitest'

import { fileDiffVsHead, reviewDiff } from './git-review-ops'

const SECRET = 'outside-the-repo-secret'
const tempDirs: string[] = []

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    fs.rmSync(dir, { force: true, recursive: true })
  }
})

function makeTempDir(prefix: string) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix))

  tempDirs.push(dir)

  return dir
}

// A committed repo with one untracked file, a secret file next to (not inside)
// the repo, and symlinks that point at the secret from inside the repo.
function makeFixture() {
  const parent = makeTempDir('hermes-review-paths-')
  const dir = path.join(parent, 'repo')
  const outside = path.join(parent, 'outside')
  const git = (...args: string[]) => execFileSync('git', args, { cwd: dir })

  fs.mkdirSync(dir)
  fs.mkdirSync(outside)
  fs.writeFileSync(path.join(outside, 'secret.txt'), `${SECRET}\n`)
  git('init', '-q')
  git('config', 'user.email', 'hermes-test@example.com')
  git('config', 'user.name', 'Hermes Test')
  git('config', 'commit.gpgsign', 'false')
  fs.writeFileSync(path.join(dir, 'tracked.txt'), 'tracked\n')
  git('add', 'tracked.txt')
  git('commit', '-qm', 'initial')
  fs.writeFileSync(path.join(dir, 'new.txt'), 'brand new\n')
  fs.mkdirSync(path.join(dir, 'sub'))
  fs.writeFileSync(path.join(dir, 'sub', 'nested.txt'), 'nested\n')
  fs.symlinkSync(outside, path.join(dir, 'escape-dir'))
  fs.symlinkSync(path.join(outside, 'secret.txt'), path.join(dir, 'escape-file'))

  return { dir, outside }
}

function hostilePaths(dir: string, outside: string) {
  return [
    path.join(outside, 'secret.txt'),
    '../outside/secret.txt',
    `sub/../../outside/secret.txt`,
    'escape-dir/secret.txt',
    'escape-file',
    path.join(dir, 'escape-dir', 'secret.txt'),
    '.git/config',
    'missing.txt'
  ]
}

test('reviewDiff: the untracked fallback refuses paths that leave the repo', async () => {
  const { dir, outside } = makeFixture()

  // Review rows are repo-relative, so an absolute path is refused even when it
  // points inside the repo.
  for (const filePath of [...hostilePaths(dir, outside), path.join(dir, 'new.txt')]) {
    const diff = String(await reviewDiff(dir, filePath, 'uncommitted', null, false, 'git'))

    assert.equal(diff, '', filePath)
  }
})

test('fileDiffVsHead: the untracked fallback refuses paths that leave the repo', async () => {
  const { dir, outside } = makeFixture()

  for (const filePath of hostilePaths(dir, outside)) {
    const diff = String(await fileDiffVsHead(dir, filePath, 'git'))

    assert.equal(diff, '', filePath)
  }
})

test('untracked files inside the repo still diff as all-add', async () => {
  const { dir } = makeFixture()

  fs.symlinkSync('new.txt', path.join(dir, 'inside-link'))

  for (const [filePath, line] of [
    ['new.txt', '+brand new'],
    ['sub/nested.txt', '+nested'],
    ['inside-link', '+new.txt']
  ]) {
    assert.ok(String(await reviewDiff(dir, filePath, 'uncommitted', null, false, 'git')).includes(line), filePath)
    assert.ok(String(await fileDiffVsHead(dir, filePath, 'git')).includes(line), filePath)
  }

  // A repo opened at a subdirectory resolves paths against that directory.
  assert.ok(
    String(await reviewDiff(path.join(dir, 'sub'), 'nested.txt', 'uncommitted', null, false, 'git')).includes('+nested')
  )

  // The file preview names the file by its absolute path.
  assert.ok(String(await fileDiffVsHead(dir, path.join(dir, 'sub', 'nested.txt'), 'git')).includes('+nested'))
})
