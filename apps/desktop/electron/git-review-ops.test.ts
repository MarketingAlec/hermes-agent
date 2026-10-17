import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { afterEach, test } from 'vitest'

import {
  gitFor,
  repoStatus,
  resolveRenamePath,
  REVIEW_FILE_CAP,
  reviewCommit,
  reviewDiff,
  reviewList,
  reviewPush,
  reviewRevParse,
  reviewStage
} from './git-review-ops'

const tempDirs: string[] = []

afterEach(() => {
  for (const dir of tempDirs.splice(0)) {
    fs.rmSync(dir, { force: true, recursive: true })
  }
})

function makeRepo() {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-desktop-git-status-'))

  tempDirs.push(dir)
  execFileSync('git', ['init', '-q'], { cwd: dir })
  execFileSync('git', ['config', 'user.email', 'hermes-test@example.com'], { cwd: dir })
  execFileSync('git', ['config', 'user.name', 'Hermes Test'], { cwd: dir })
  // A developer's global signing / fsmonitor setup must not reach fixture repos.
  execFileSync('git', ['config', 'commit.gpgsign', 'false'], { cwd: dir })
  execFileSync('git', ['config', 'core.fsmonitor', 'false'], { cwd: dir })
  fs.writeFileSync(path.join(dir, 'tracked.txt'), 'tracked\n')
  execFileSync('git', ['add', 'tracked.txt'], { cwd: dir })
  execFileSync('git', ['commit', '-qm', 'initial'], { cwd: dir })

  return dir
}

function makeTempDir(prefix: string) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), prefix))

  tempDirs.push(dir)

  return dir
}

const git = (cwd: string, args: string[]) => execFileSync('git', args, { cwd, encoding: 'utf8' }).trim()

// Option-shaped values a compromised renderer could put in a ref slot. Each one
// would act on the machine if git parsed it as a flag instead of a revision.
function hostileRefs(sink: string) {
  return [
    `--output=${path.join(sink, 'diff-output')}`,
    `-c core.pager=touch ${path.join(sink, 'pager')}`,
    `-ccore.pager=touch ${path.join(sink, 'pager-attached')}`,
    `--upload-pack=touch ${path.join(sink, 'upload-pack')}`,
    '-'
  ]
}

test('ref-taking review ops reject option-shaped refs without running them', async () => {
  const dir = makeRepo()
  const sink = makeTempDir('hermes-desktop-git-sink-')

  fs.writeFileSync(path.join(dir, 'tracked.txt'), 'changed\n')

  for (const ref of hostileRefs(sink)) {
    assert.equal(await reviewRevParse(dir, ref, 'git'), null, ref)
    assert.deepEqual(await reviewList(dir, 'lastTurn', ref, 'git'), { files: [], base: null }, ref)
    assert.equal(await reviewDiff(dir, 'tracked.txt', 'lastTurn', ref, false, 'git'), '', ref)
  }

  assert.deepEqual(fs.readdirSync(sink), [])
})

test('ref-taking review ops reject values that are not a commit in the repo', async () => {
  const dir = makeRepo()

  for (const ref of ['no-such-branch', 'HEAD:tracked.txt', 'HEAD~1..HEAD']) {
    assert.equal(await reviewRevParse(dir, ref, 'git'), null, ref)
    assert.deepEqual(await reviewList(dir, 'lastTurn', ref, 'git'), { files: [], base: null }, ref)
    assert.equal(await reviewDiff(dir, 'tracked.txt', 'lastTurn', ref, false, 'git'), '', ref)
  }
})

test('ref-taking review ops resolve ordinary refs to the commit they name', async () => {
  const dir = makeRepo()
  const base = git(dir, ['rev-parse', 'HEAD'])

  git(dir, ['tag', 'v1'])
  git(dir, ['branch', 'feature/x'])
  fs.writeFileSync(path.join(dir, 'tracked.txt'), 'second\n')
  git(dir, ['commit', '-qam', 'second'])

  const head = git(dir, ['rev-parse', 'HEAD'])

  assert.equal(await reviewRevParse(dir, null, 'git'), head)
  assert.equal(await reviewRevParse(dir, 'HEAD', 'git'), head)

  for (const ref of [base, base.slice(0, 10), 'v1', 'feature/x', 'HEAD~1', 'HEAD^']) {
    assert.equal(await reviewRevParse(dir, ref, 'git'), base, ref)

    const list = await reviewList(dir, 'lastTurn', ref, 'git')

    assert.equal(list.base, base, ref)
    assert.deepEqual(
      list.files.map(file => file.path),
      ['tracked.txt'],
      ref
    )
    assert.match(String(await reviewDiff(dir, 'tracked.txt', 'lastTurn', ref, false, 'git')), /^\+second$/m, ref)
  }
})

test('option-shaped file names and commit messages are committed literally', async () => {
  const dir = makeRepo()
  const sink = makeTempDir('hermes-desktop-git-sink-')
  // File names can't hold a separator, so their payload targets the repo cwd.
  const fileNames = ['--upload-pack=touch pwned-by-file', '-c']
  const message = `--upload-pack=touch ${path.join(sink, 'message')}`

  for (const name of fileNames) {
    fs.writeFileSync(path.join(dir, name), `${name}\n`)
  }

  for (const name of fileNames) {
    await reviewStage(dir, name, 'git')
    const diff = String(await reviewDiff(dir, name, 'uncommitted', null, true, 'git'))

    assert.match(diff, new RegExp(`^\\+${name}$`, 'm'))
  }

  await reviewCommit(dir, `${message}\n\n-c core.pager=touch ${path.join(sink, 'body')}`, false, 'git')

  assert.equal(git(dir, ['log', '-1', '--format=%s']), message)
  assert.deepEqual(git(dir, ['show', '--name-only', '--format=', 'HEAD']).split('\n').sort(), [...fileNames].sort())
  assert.equal(fs.existsSync(path.join(dir, 'pwned-by-file')), false)
  assert.deepEqual(fs.readdirSync(sink), [])
})

test('push refuses hostile remote URLs without running them', async () => {
  const sink = makeTempDir('hermes-desktop-git-sink-')

  const urls = [
    `--upload-pack=touch ${path.join(sink, 'upload-pack')}`,
    `ssh://-oProxyCommand=touch%20${path.join(sink, 'proxy')}/repo.git`,
    `ext::sh -c touch% ${path.join(sink, 'ext')}`
  ]

  for (const url of urls) {
    const dir = makeRepo()

    git(dir, ['config', 'remote.origin.url', url])
    await assert.rejects(reviewPush(dir, 'git'), undefined, url)
  }

  assert.deepEqual(fs.readdirSync(sink), [])
})

test('resolveRenamePath: plain path is unchanged', () => {
  assert.equal(resolveRenamePath('src/a.ts'), 'src/a.ts')
})

test('gitFor accepts an internally resolved git binary path containing spaces', () => {
  assert.doesNotThrow(() => gitFor(process.cwd(), 'C:\\Program Files\\Git\\cmd\\git.exe'))
})

test('gitFor runs git through a spaced binary path', async () => {
  if (process.platform !== 'win32') {
    return
  }

  const gitBin = path.join(process.env.ProgramFiles || String.raw`C:\Program Files`, 'Git', 'cmd', 'git.exe')

  if (!fs.existsSync(gitBin)) {
    return
  }

  const repo = makeRepo()

  fs.writeFileSync(path.join(repo, 'changed.txt'), 'review me\n')

  const status = await gitFor(repo, gitBin).status()

  assert.equal(status.not_added.includes('changed.txt'), true)
})

test('resolveRenamePath: simple rename resolves to the new path', () => {
  assert.equal(resolveRenamePath('old.ts => new.ts'), 'new.ts')
})

test('resolveRenamePath: brace rename resolves to the new path', () => {
  assert.equal(resolveRenamePath('src/{old => new}/file.ts'), 'src/new/file.ts')
})

test('resolveRenamePath: brace rename collapsing a segment', () => {
  assert.equal(resolveRenamePath('src/{lib => }/file.ts'), 'src/file.ts')
})

test('repoStatus reports an untracked directory without recursively listing its contents', async () => {
  const dir = makeRepo()
  const nested = path.join(dir, 'generated', 'deep')

  fs.mkdirSync(nested, { recursive: true })
  fs.writeFileSync(path.join(nested, 'large-output.txt'), 'generated\n')

  const status = await repoStatus(dir, 'git')

  assert.ok(status)
  assert.equal(status.untracked, 1)
  assert.equal(status.changed, 1)
  assert.deepEqual(
    status.files.map(file => file.path),
    ['generated/']
  )
})

test('reviewList reports an untracked directory without recursively listing its contents', async () => {
  const dir = makeRepo()
  const nested = path.join(dir, 'browser-profile', 'Default', 'Cache')

  fs.mkdirSync(nested, { recursive: true })

  for (let i = 0; i < 20; i++) {
    fs.writeFileSync(path.join(nested, `cache-${i}.bin`), 'generated\n')
  }

  const result = await reviewList(dir, 'uncommitted', null, 'git')

  assert.deepEqual(
    result.files.map(file => file.path),
    ['browser-profile/']
  )
})

test('reviewList caps the file payload returned to the renderer', async () => {
  const dir = makeRepo()

  for (let i = 0; i < REVIEW_FILE_CAP + 10; i++) {
    fs.writeFileSync(path.join(dir, `untracked-${String(i).padStart(4, '0')}.txt`), 'generated\n')
  }

  const result = await reviewList(dir, 'uncommitted', null, 'git')

  assert.equal(result.files.length, REVIEW_FILE_CAP)
})
