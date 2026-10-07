module.exports = {
  defaultIgnores: false,
  extends: ['@commitlint/config-conventional'],
  rules: {
    'type-enum': [
      2,
      'always',
      [
        'feat',
        'fix',
        'refactor',
        'docs',
        'test',
        'build',
        'ci',
        'chore',
        'style',
        'perf',
        'revert',
      ],
    ],
    'header-max-length': [2, 'always', 100],
    'subject-case': [0],
    'breaking-change-exclamation-mark': [0],
    'subject-chinese': [2, 'always'],
  },
  plugins: [
    {
      rules: {
        'subject-chinese': ({ subject }) => [
          typeof subject === 'string' && /[\u3400-\u9fff]/u.test(subject),
          '提交摘要需要说明中文变更内容',
        ],
      },
    },
  ],
}
