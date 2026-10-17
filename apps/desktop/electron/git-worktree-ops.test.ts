import assert from 'node:assert/strict'
import { execFileSync } from 'node:child_process'
import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'

import { test } from 'vitest'

import {
  addWorktree,
  ensureGitRepo,
  fetchRemoteBranch,
  gitVersionProblem,
  listBaseBranches,
  listBranches,
  parseWorktrees,
  sanitizeBranch,
  switchBranch
} from './git-worktree-ops'

test('sanitizeBranch: spaces → hyphens, forbidden chars dropped, edges trimmed', () => {
  assert.equal(sanitizeBranch('beach vibes'), 'beach-vibes')
  assert.equal(sanitizeBranch('feat/cool thing'), 'feat/cool-thing')
  assert.equal(sanitizeBranch('  wip~^:? '), 'wip')
  assert.equal(sanitizeBranch('///'), '')
})

test('parseWorktrees: main checkout + linked worktree', () => {
  const out = [
    'worktree /repo',
    'HEAD abc123',
    'branch refs/heads/main',
    '',
    'worktree /repo/.worktrees/feat',
    'HEAD def456',
    'branch refs/heads/hermes/feat',
    ''
  ].join('\n')

  const trees = parseWorktrees(out)

  assert.equal(trees.length, 2)
  assert.equal(trees[0].path, '/repo')
  assert.equal(trees[0].branch, 'main')
  assert.equal(trees[1].path, '/repo/.worktrees/feat')
  assert.equal(trees[1].branch, 'hermes/feat')
})

test('parseWorktrees: detached + locked flags', () => {
  const out = ['worktree /repo/wt', 'HEAD abc', 'detached', 'locked reason', ''].join('\n')
  const trees = parseWorktrees(out)

  assert.equal(trees.length, 1)
  assert.equal(trees[0].detached, true)
  assert.equal(trees[0].locked, true)
  assert.equal(trees[0].branch, null)
})

test('parseWorktrees: empty input', () => {
  assert.deepEqual(parseWorktrees(''), [])
})

test('ensureGitRepo: inits a plain dir with a root commit so worktrees branch', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-wt-'))
  const git = (...args) => execFileSync('git', args, { cwd: dir }).toString().trim()

  try {
    await ensureGitRepo('git', dir)
    assert.match(git('rev-parse', '--verify', 'HEAD'), /^[0-9a-f]{7,}$/)

    // The whole point: a worktree can now branch off the seeded root commit.
    execFileSync('git', ['worktree', 'add', '-b', 'wt', path.join(dir, '.worktrees', 'wt')], { cwd: dir })
    assert.ok(fs.existsSync(path.join(dir, '.worktrees', 'wt')))

    // Idempotent: an already-committed repo gets no extra commit.
    await ensureGitRepo('git', dir)
    assert.equal(git('rev-list', '--count', 'HEAD'), '1')
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('switchBranch: switches a normal checkout branch', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-switch-'))
  const git = (...args) => execFileSync('git', args, { cwd: dir }).toString().trim()

  try {
    await ensureGitRepo('git', dir)
    execFileSync('git', ['branch', 'feature'], { cwd: dir })

    await switchBranch(dir, 'feature', 'git')

    assert.equal(git('branch', '--show-current'), 'feature')
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('listBranches: lists locals and flags the checked-out branch', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-branches-'))

  try {
    await ensureGitRepo('git', dir)
    const current = execFileSync('git', ['branch', '--show-current'], { cwd: dir }).toString().trim()
    execFileSync('git', ['branch', 'feature'], { cwd: dir })

    const branches = await listBranches(dir, 'git')
    const names = branches.map(b => b.name).sort()

    assert.deepEqual(names, [current, 'feature'].sort())
    // The repo's own checkout is flagged; the unused branch is convertible.
    assert.equal(branches.find(b => b.name === current).checkedOut, true)
    assert.equal(branches.find(b => b.name === current).isDefault, true)
    assert.equal(fs.realpathSync(branches.find(b => b.name === current).worktreePath), fs.realpathSync(dir))
    assert.equal(branches.find(b => b.name === 'feature').checkedOut, false)
    assert.equal(branches.find(b => b.name === 'feature').isDefault, false)
    assert.equal(branches.find(b => b.name === 'feature').worktreePath, null)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('listBranches: flags a free default branch as default, not checked out', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-branches-default-'))
  const git = (...args) => execFileSync('git', args, { cwd: dir }).toString().trim()

  try {
    await ensureGitRepo('git', dir)
    const trunk = git('branch', '--show-current')
    execFileSync('git', ['switch', '-c', 'rawr'], { cwd: dir })

    const branches = await listBranches(dir, 'git')
    const defaultBranch = branches.find(b => b.name === trunk)

    assert.equal(defaultBranch.checkedOut, false)
    assert.equal(defaultBranch.isDefault, true)
    assert.equal(defaultBranch.worktreePath, null)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('listBranches: a branch claimed by a worktree is flagged checked out', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-branches-wt-'))

  try {
    await ensureGitRepo('git', dir)
    execFileSync('git', ['branch', 'feature'], { cwd: dir })
    // addWorktree converts the existing "feature" branch into a worktree.
    const result = await addWorktree(dir, { existingBranch: 'feature' }, 'git')

    assert.equal(result.branch, 'feature')
    assert.ok(fs.existsSync(result.path))

    const branches = await listBranches(dir, 'git')

    assert.equal(branches.find(b => b.name === 'feature').checkedOut, true)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('listBranches: empty on a non-repo path', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-nonrepo-'))

  try {
    assert.deepEqual(await listBranches(dir, 'git'), [])
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('addWorktree: existingBranch checks the branch out without a new branch', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-convert-'))
  const git = (...args) => execFileSync('git', args, { cwd: dir }).toString().trim()

  try {
    await ensureGitRepo('git', dir)
    execFileSync('git', ['branch', 'cool/feature'], { cwd: dir })

    const before = git('branch', '--list').split('\n').length
    const result = await addWorktree(dir, { existingBranch: 'cool/feature' }, 'git')

    // No new branch was created — only the existing one is checked out.
    assert.equal(git('branch', '--list').split('\n').length, before)
    assert.equal(result.branch, 'cool/feature')
    // Dir is named off the branch slug, nested under the main repo's .worktrees.
    assert.match(result.path, /[/\\]\.worktrees[/\\]cool-feature/)
    assert.equal(
      execFileSync('git', ['branch', '--show-current'], { cwd: result.path }).toString().trim(),
      'cool/feature'
    )
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('addWorktree: existing default branch switches the main checkout, not .worktrees/main', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-convert-default-'))
  const git = (...args) => execFileSync('git', args, { cwd: dir }).toString().trim()

  try {
    await ensureGitRepo('git', dir)
    const trunk = git('branch', '--show-current')
    execFileSync('git', ['switch', '-c', 'rawr'], { cwd: dir })

    const result = await addWorktree(dir, { existingBranch: trunk }, 'git')

    assert.equal(result.branch, trunk)
    assert.equal(fs.realpathSync(result.path), fs.realpathSync(dir))
    assert.equal(git('branch', '--show-current'), trunk)
    assert.equal(fs.existsSync(path.join(dir, '.worktrees', trunk)), false)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('listBaseBranches: lists local branches and flags the default', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-base-branches-'))
  const git = (...args) => execFileSync('git', args, { cwd: dir }).toString().trim()

  try {
    await ensureGitRepo('git', dir)
    const trunk = git('branch', '--show-current')
    execFileSync('git', ['branch', 'feature'], { cwd: dir })

    const branches = await listBaseBranches(dir, 'git')
    const names = branches.map(b => b.name).sort()

    assert.deepEqual(names, [trunk, 'feature'].sort())
    // No remote → all local.
    assert.equal(
      branches.every(b => !b.isRemote),
      true
    )
    // The trunk is flagged as the default.
    assert.equal(branches.find(b => b.name === trunk).isDefault, true)
    assert.equal(branches.find(b => b.name === 'feature').isDefault, false)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('listBaseBranches: empty on a non-repo path', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-base-nonrepo-'))

  try {
    assert.deepEqual(await listBaseBranches(dir, 'git'), [])
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('addWorktree: base param branches off a specified local branch', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-base-add-'))
  const git = (...args) => execFileSync('git', args, { cwd: dir }).toString().trim()

  try {
    await ensureGitRepo('git', dir)
    execFileSync('git', ['branch', 'staging'], { cwd: dir })

    const result = await addWorktree(
      dir,
      { base: 'staging', branch: 'new-from-staging', name: 'new-from-staging' },
      'git'
    )

    assert.equal(result.branch, 'new-from-staging')
    assert.equal(git('-C', result.path, 'merge-base', 'HEAD', 'staging').length > 0, true)
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('addWorktree: base origin/main does not set up upstream tracking', async () => {
  // Two repos: a bare "remote" and a clone, so origin/main resolves as a
  // remote-tracking ref — the condition that triggers auto-tracking.
  const remoteDir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-remote-'))
  const cloneDir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-clone-'))
  const git = (...args) => execFileSync('git', args, { cwd: cloneDir }).toString().trim()

  try {
    // Seed the remote with a commit on main. Inline identity so it works
    // on CI runners with no global git config.
    execFileSync('git', ['init', '-b', 'main', remoteDir])
    execFileSync('git', [
      '-C',
      remoteDir,
      '-c',
      'user.email=hermes@localhost',
      '-c',
      'user.name=Hermes',
      'commit',
      '--allow-empty',
      '-m',
      'root'
    ])

    // Clone so origin/main exists as a remote-tracking ref.
    execFileSync('git', ['clone', remoteDir, cloneDir])

    const result = await addWorktree(
      cloneDir,
      { base: 'origin/main', branch: 'feature-branch', name: 'feature-branch' },
      'git'
    )

    assert.equal(result.branch, 'feature-branch')

    // The new branch must NOT have an upstream — like `git checkout origin/main
    // && git checkout -b feature-branch`, not `git worktree add -b … origin/main`.
    let hasUpstream = true

    try {
      execFileSync('git', ['-C', result.path, 'rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'])
    } catch {
      hasUpstream = false
    }

    assert.equal(hasUpstream, false)
  } finally {
    fs.rmSync(remoteDir, { recursive: true, force: true })
    fs.rmSync(cloneDir, { recursive: true, force: true })
  }
})

// A pair of repos: a bare "remote" with `main` and the extra branches in
// `branches`, plus a clone of it. Returns both paths. The caller must remove
// them.
function seedRemoteAndClone(label, branches) {
  const remoteDir = fs.mkdtempSync(path.join(os.tmpdir(), `hermes-${label}-remote-`))
  const cloneDir = fs.mkdtempSync(path.join(os.tmpdir(), `hermes-${label}-clone-`))

  const remoteGit = (...args) =>
    execFileSync('git', ['-C', remoteDir, ...args])
      .toString()
      .trim()

  execFileSync('git', ['init', '-b', 'main', remoteDir])
  remoteGit('-c', 'user.email=hermes@localhost', '-c', 'user.name=Hermes', 'commit', '--allow-empty', '-m', 'root')

  for (const branch of branches) {
    remoteGit('branch', branch)
  }

  execFileSync('git', ['clone', remoteDir, cloneDir])

  return { cloneDir, remoteDir }
}

test('listBranches: offers remote branches that have no local counterpart', async () => {
  const { cloneDir, remoteDir } = seedRemoteAndClone('branches-remote', ['teammate-work'])

  try {
    const branches = await listBranches(cloneDir, 'git')
    const byName = new Map(branches.map(b => [b.name, b]))

    // The teammate's branch is only on the remote. The list therefore offers it
    // by its remote-tracking name, with a flag that lets the UI say "track
    // remote".
    const remoteOnly = byName.get('origin/teammate-work')

    assert.ok(remoteOnly)
    assert.equal(remoteOnly.isRemote, true)
    assert.equal(remoteOnly.checkedOut, false)
    assert.equal(remoteOnly.isDefault, false)
    assert.equal(remoteOnly.worktreePath, null)

    // `main` is checked out locally, so it shows once as a local branch.
    // "origin/main" is a duplicate of a branch that is already in the list.
    assert.equal(byName.get('main').isRemote, false)
    assert.equal(byName.has('origin/main'), false)

    // "origin/HEAD" is an alias for the default branch of the remote. It is not
    // a branch.
    assert.equal(
      branches.some(b => b.name.endsWith('/HEAD')),
      false
    )
  } finally {
    fs.rmSync(remoteDir, { recursive: true, force: true })
    fs.rmSync(cloneDir, { recursive: true, force: true })
  }
})

test('addWorktree: a remote branch becomes a local branch tracking it', async () => {
  const { cloneDir, remoteDir } = seedRemoteAndClone('convert-remote', ['teammate-work'])

  try {
    const result = await addWorktree(cloneDir, { existingBranch: 'origin/teammate-work' }, 'git')

    const inTree = (...args) =>
      execFileSync('git', ['-C', result.path, ...args])
        .toString()
        .trim()

    // The worktree is on a local branch that has the name of the remote one. It
    // is not on a detached HEAD, which is the result of a checkout of
    // "origin/teammate-work".
    assert.equal(result.branch, 'teammate-work')
    assert.equal(inTree('branch', '--show-current'), 'teammate-work')
    assert.match(result.path, /[/\\]\.worktrees[/\\]teammate-work/)

    // The branch tracks the remote branch, so push and pull work with no more
    // setup.
    assert.equal(inTree('rev-parse', '--abbrev-ref', '--symbolic-full-name', '@{u}'), 'origin/teammate-work')
  } finally {
    fs.rmSync(remoteDir, { recursive: true, force: true })
    fs.rmSync(cloneDir, { recursive: true, force: true })
  }
})

test('addWorktree: a remote default branch gets its own worktree, not a home switch', async () => {
  const { cloneDir, remoteDir } = seedRemoteAndClone('convert-remote-default', [])

  const git = (...args) =>
    execFileSync('git', ['-C', cloneDir, ...args])
      .toString()
      .trim()

  try {
    // Move the main checkout off `main`, which makes "origin/main" convertible.
    // The local `main` is then free, but the request names the remote-tracking
    // ref.
    git('switch', '-c', 'rawr')
    git('branch', '-D', 'main')

    const result = await addWorktree(cloneDir, { existingBranch: 'origin/main' }, 'git')

    // "switch home" applies to a local default branch. A remote ref always gets
    // a new worktree, so the main checkout stays where the user put it.
    assert.equal(result.branch, 'main')
    assert.notEqual(fs.realpathSync(result.path), fs.realpathSync(cloneDir))
    assert.equal(git('branch', '--show-current'), 'rawr')
  } finally {
    fs.rmSync(remoteDir, { recursive: true, force: true })
    fs.rmSync(cloneDir, { recursive: true, force: true })
  }
})

test('switchBranch: non-repo dir short-circuits instead of throwing', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-sw-'))

  try {
    // A plain folder pinned as a project (no .git): its lane label is the
    // folder basename, not a branch — switching must no-op, not error, so
    // callers like "+" new session can proceed with a plain session.
    const result = await switchBranch(dir, '国创大赛', 'git')

    assert.deepEqual(result, { branch: null })
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

test('switchBranch: repo dir still validates the branch name and switches', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-sw-'))

  try {
    execFileSync('git', ['init', '-b', 'main'], { cwd: dir })
    execFileSync('git', ['config', 'user.email', 't@example.com'], { cwd: dir })
    execFileSync('git', ['config', 'user.name', 'test'], { cwd: dir })
    execFileSync('git', ['commit', '--allow-empty', '-m', 'root'], { cwd: dir })

    // Existing behaviour preserved: an illegal branch name still errors.
    await assert.rejects(() => switchBranch(dir, '///', 'git'), /Branch name is required/)

    // And switching to a real branch still works.
    const result = await switchBranch(dir, 'main', 'git')
    assert.deepEqual(result, { branch: 'main' })
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
  }
})

// An executable that records that it ran. Every hostile value below names it,
// so anything besides the payload in `sink` means git parsed caller text as an
// option and executed it.
function makeSink(label) {
  const sink = fs.mkdtempSync(path.join(os.tmpdir(), `hermes-${label}-sink-`))
  const payload = path.join(sink, 'payload')

  fs.writeFileSync(payload, `#!/bin/sh\ntouch "${path.join(sink, 'ran')}"\n`, { mode: 0o755 })

  return { executed: () => fs.readdirSync(sink).filter(name => name !== 'payload'), payload, sink }
}

function hostileValues(payload) {
  return [
    `--upload-pack=${payload}`,
    `-c core.pager=${payload}`,
    `-ccore.pager=${payload}`,
    `--output=${payload}.out`,
    '-'
  ]
}

test('addWorktree: option-shaped bases are rejected without running anything', async () => {
  const { cloneDir, remoteDir } = seedRemoteAndClone('hostile-base', [])
  const { executed, payload, sink } = makeSink('hostile-base')

  try {
    // The sink is live: the same payload handed to git as an option does run.
    try {
      execFileSync('git', ['fetch', 'origin', `--upload-pack=${payload}`], { cwd: cloneDir, stdio: 'ignore' })
    } catch {
      // upload-pack is not a real server; only the side effect matters.
    }

    assert.deepEqual(executed(), ['ran'])
    fs.rmSync(path.join(sink, 'ran'))

    const bases = hostileValues(payload).flatMap(value => [value, `origin/${value}`])

    for (const base of bases) {
      await assert.rejects(addWorktree(cloneDir, { base, branch: 'hostile' }, 'git'), undefined, base)
    }

    assert.deepEqual(executed(), [])
    assert.equal(
      execFileSync('git', ['branch', '--list', 'hostile'], { cwd: cloneDir }).toString().trim(),
      '',
      'no branch was created from a hostile base'
    )
  } finally {
    for (const dir of [remoteDir, cloneDir, sink]) {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  }
})

test('addWorktree: a base names a commit, and the worktree starts at that commit', async () => {
  const { cloneDir, remoteDir } = seedRemoteAndClone('base-commit', [])

  const git = (...args) =>
    execFileSync('git', ['-C', cloneDir, ...args])
      .toString()
      .trim()

  try {
    const head = git('rev-parse', 'HEAD')

    for (const base of ['no-such-branch', 'origin/no-such-branch', 'HEAD:missing.txt', 'HEAD~1..HEAD']) {
      await assert.rejects(addWorktree(cloneDir, { base, branch: 'bogus' }, 'git'), undefined, base)
    }

    for (const [base, branch] of [
      ['origin/main', 'from-remote'],
      ['main', 'from-local'],
      [head, 'from-sha']
    ]) {
      const result = await addWorktree(cloneDir, { base, branch }, 'git')

      assert.equal(git('-C', result.path, 'rev-parse', 'HEAD'), head, base)
      assert.equal(git('-C', result.path, 'branch', '--show-current'), branch, base)
    }
  } finally {
    fs.rmSync(remoteDir, { recursive: true, force: true })
    fs.rmSync(cloneDir, { recursive: true, force: true })
  }
})

test('fetchRemoteBranch: option-shaped remote and branch names never reach git as options', async () => {
  const { cloneDir, remoteDir } = seedRemoteAndClone('hostile-remote', [])
  const { executed, payload, sink } = makeSink('hostile-remote')

  try {
    // A repo's config can carry any remote name, so a configured remote is not
    // proof that the name is safe to put in argv.
    const remotes = hostileValues(payload)

    for (const remote of remotes) {
      execFileSync('git', ['config', `remote.${remote}.url`, remoteDir], { cwd: cloneDir })
    }

    for (const remote of remotes) {
      assert.equal(await fetchRemoteBranch('git', cloneDir, remote, 'main'), false, remote)
    }

    for (const branch of hostileValues(payload)) {
      assert.equal(await fetchRemoteBranch('git', cloneDir, 'origin', branch), false, branch)
    }

    assert.equal(await fetchRemoteBranch('git', cloneDir, 'not-a-remote', 'main'), false)
    assert.equal(await fetchRemoteBranch('git', cloneDir, 'origin', 'main:refs/heads/clobbered'), false)
    assert.deepEqual(executed(), [])

    assert.equal(await fetchRemoteBranch('git', cloneDir, 'origin', 'main'), true)
  } finally {
    for (const dir of [remoteDir, cloneDir, sink]) {
      fs.rmSync(dir, { recursive: true, force: true })
    }
  }
})

// Every string over an alphabet of the characters that matter at a name's edge.
function* edgeStrings(alphabet, maxLength) {
  let level = ['']

  for (let length = 1; length <= maxLength; length++) {
    level = level.flatMap(prefix => alphabet.map(char => prefix + char))
    yield* level
  }
}

test('sanitizeBranch: never returns an option-shaped name', () => {
  const inputs = [
    ...edgeStrings(['-', '.', '/', ' ', '\t', '=', '~', 'a'], 5),
    '--upload-pack=touch /tmp/x',
    '-c core.pager=touch /tmp/x',
    ' \t--x',
    '日本-',
    '\u00a0-x'
  ]

  for (const input of inputs) {
    assert.equal(sanitizeBranch(input).startsWith('-'), false, JSON.stringify(input))
  }
})

test('gitVersionProblem: git older than 2.30 is refused with the reason', () => {
  for (const version of ['git version 2.23.0', 'git version 2.24.4', 'git version 2.29.3', 'git version 1.9']) {
    assert.match(gitVersionProblem(version), /too old.*Git 2\.30\.0 or newer/, version)
  }

  for (const version of [
    'git version 2.30.0',
    'git version 2.43.0',
    'git version 2.39.3 (Apple Git-145)',
    'git version 2.45.1.windows.1',
    'git version 3.0.0',
    'not git output'
  ]) {
    assert.equal(gitVersionProblem(version), '', version)
  }
})

test('addWorktree: an old git fails the flow with a clear error and creates nothing', async () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-old-git-'))
  const bin = fs.mkdtempSync(path.join(os.tmpdir(), 'hermes-old-git-bin-'))
  const oldGit = path.join(bin, 'git')

  fs.writeFileSync(
    oldGit,
    '#!/bin/sh\n[ "$1" = "--version" ] && { echo "git version 2.23.0"; exit 0; }\nexec git "$@"\n',
    {
      mode: 0o755
    }
  )

  try {
    await ensureGitRepo('git', dir)

    for (const options of [{ branch: 'fresh' }, { base: 'HEAD', branch: 'fresh' }, { existingBranch: 'other' }]) {
      await assert.rejects(addWorktree(dir, options, oldGit), /Git 2\.23\.0 is too old.*Git 2\.30\.0 or newer/)
    }

    assert.equal(fs.existsSync(path.join(dir, '.worktrees')), false)
    assert.equal(execFileSync('git', ['branch', '--list', 'fresh'], { cwd: dir }).toString().trim(), '')
  } finally {
    fs.rmSync(dir, { recursive: true, force: true })
    fs.rmSync(bin, { recursive: true, force: true })
  }
})
