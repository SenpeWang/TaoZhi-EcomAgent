/**
 * Conventional Commits 提交信息校验配置。
 * 限定 11 类提交类型，要求摘要包含中文且不超过 100 字符，
 * 通过自定义 subject-chinese 规则拦截缺少中文说明的提交。
 */
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
