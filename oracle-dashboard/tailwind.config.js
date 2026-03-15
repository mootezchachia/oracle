/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        'bg-0': '#030506',
        'bg-1': '#0a0e13',
        'bg-2': '#111820',
        'bg-3': '#1a2332',
        'border': '#1e2a3a',
        'border-bright': '#2d4156',
        'gold': '#f0b429',
        'gold-dim': '#b8860b',
        'green': '#3fb950',
        'green-dim': '#1a4d2e',
        'red': '#f85149',
        'red-dim': '#5c1d1a',
        'blue': '#58a6ff',
        'cyan': '#39d353',
        'purple': '#bc8cff',
        'orange': '#f0883e',
        'text-0': '#e8edf4',
        'text-1': '#a0aec0',
        'text-2': '#5a6a7e',
      },
      fontFamily: {
        mono: ['JetBrains Mono', 'Fira Code', 'Cascadia Mono', 'monospace'],
      },
    },
  },
  plugins: [],
}
