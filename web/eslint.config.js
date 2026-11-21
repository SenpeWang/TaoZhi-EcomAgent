import js from '@eslint/js'
import vue from 'eslint-plugin-vue'
import ts from 'typescript-eslint'
import prettier from 'eslint-config-prettier'
import jsdoc from 'eslint-plugin-jsdoc'
import globals from 'globals'

const documentationComments = {
  rules: {
    'only-jsdoc': {
      create(context) {
        return {
          'Program:exit'() {
            const source = context.sourceCode
            const templateComments = source.parserServices.getDocumentFragment?.()?.comments ?? []
            const comments = [...source.getAllComments(), ...templateComments]
            for (const comment of comments) {
              if (comment.type === 'Block' && comment.value.startsWith('*')) continue
              context.report({ loc: comment.loc, message: '源码说明请使用 JSDoc 文档块' })
            }
          },
        }
      },
    },
  },
}

export default ts.config(
  { ignores: ['dist/**', 'node_modules/**', '.build-*/**'] },
  js.configs.recommended,
  {
    files: ['**/*.{js,cjs,mjs,ts,vue}'],
    plugins: { documentation: documentationComments },
    rules: { 'documentation/only-jsdoc': 'error' },
  },
  ...ts.configs.recommended,
  ...vue.configs['flat/recommended'],
  {
    files: ['src/**/*.{ts,vue}'],
    languageOptions: {
      globals: globals.browser,
      parserOptions: { parser: ts.parser },
    },
    plugins: { jsdoc },
    rules: {
      'no-empty': ['error', { allowEmptyCatch: true }],
      'jsdoc/require-jsdoc': [
        'error',
        {
          require: {
            FunctionDeclaration: true,
            ArrowFunctionExpression: true,
            FunctionExpression: true,
          },
          contexts: ['TSDeclareFunction'],
        },
      ],
      'jsdoc/require-description': 'error',
      'jsdoc/require-param': 'error',
      'jsdoc/require-param-description': 'error',
      'jsdoc/require-returns': 'error',
      'jsdoc/require-returns-description': 'error',
      'jsdoc/check-tag-names': 'error',
      'jsdoc/match-description': [
        'error',
        {
          matchDescription: '[\\s\\S]*[\\u3400-\\u9fff][\\s\\S]*',
          message: '关键功能的 JSDoc 说明必须包含中文',
        },
      ],
      '@typescript-eslint/naming-convention': [
        'error',
        { selector: 'function', format: ['camelCase'] },
        { selector: 'variable', format: ['camelCase', 'PascalCase', 'UPPER_CASE'] },
        { selector: 'parameter', format: ['camelCase'], leadingUnderscore: 'allow' },
        { selector: 'typeLike', format: ['PascalCase'] },
      ],
      'vue/component-definition-name-casing': ['error', 'PascalCase'],
      'vue/match-component-file-name': 'error',
      'vue/component-name-in-template-casing': [
        'error',
        'PascalCase',
        { registeredComponentsOnly: true },
      ],
    },
  },
  {
    files: ['*.{js,cjs,mjs}'],
    languageOptions: { globals: globals.node },
  },
  {
    files: ['src/views/MarkdownReport.vue'],
    rules: { 'vue/no-v-html': ['error', { ignorePattern: '^renderedReportHtml$' }] },
  },
  prettier,
)
