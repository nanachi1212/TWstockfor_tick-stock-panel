import js from '@eslint/js'
import tsPlugin from 'typescript-eslint'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'

export default tsPlugin.config(
  { ignores: ['dist', 'node_modules', '*.config.js', '*.config.ts', 'dist/**'] },
  {
    extends: [js.configs.recommended, ...tsPlugin.configs.recommended],
    files: ['**/*.{ts,tsx}'],
    plugins: {
      'react-hooks': reactHooks,
      'react-refresh': reactRefresh,
    },
    rules: {
      'react-hooks/rules-of-hooks': 'error',
      'react-hooks/exhaustive-deps': 'warn',
      'react-refresh/only-export-components': 'off',
      '@typescript-eslint/no-explicit-any': 'off',
      '@typescript-eslint/no-unused-vars': 'off',
      '@typescript-eslint/no-empty-object-type': 'off',
      '@typescript-eslint/ban-ts-comment': 'off',
      'no-empty': 'off',
      'no-case-declarations': 'off',
      'prefer-const': 'off',
      'no-useless-escape': 'off',
      'no-control-regex': 'off',
      'no-useless-assignment': 'off',
    },
  }
)
