/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,ts,jsx,tsx}'],
  theme: {
    extend: {
      fontFamily: {
        sans: ['DM Sans', 'system-ui', 'sans-serif'],
      },
      colors: {
        brand: {
          DEFAULT: '#2F6B4F',
          hover:   '#245840',
          tint:    '#f0f7f3',
          light:   '#e8f4ed',
          muted:   'rgba(47,107,79,0.08)',
          subtle:  'rgba(47,107,79,0.12)',
        },
        surface: {
          DEFAULT: '#FFFFFF',
          subtle:  '#F7F7F8',
          muted:   '#F0EFED',
        },
      },
      boxShadow: {
        subtle:      '0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04)',
        card:        '0 2px 12px rgba(0,0,0,0.07), 0 1px 3px rgba(0,0,0,0.04)',
        input:       '0 0 0 1px rgba(0,0,0,0.08), 0 4px 24px rgba(0,0,0,0.08)',
        'input-focus':'0 0 0 2px rgba(47,107,79,0.22), 0 4px 24px rgba(0,0,0,0.08)',
      },
      keyframes: {
        'msg-in': {
          '0%':   { opacity: '0', transform: 'translateY(8px)' },
          '100%': { opacity: '1', transform: 'translateY(0)' },
        },
        'fade-in': {
          '0%':   { opacity: '0' },
          '100%': { opacity: '1' },
        },
        'dot-bounce': {
          '0%,80%,100%': { transform: 'translateY(0)',    opacity: '0.35' },
          '40%':          { transform: 'translateY(-5px)', opacity: '1' },
        },
      },
      animation: {
        'msg-in':     'msg-in 0.18s ease-out',
        'fade-in':    'fade-in 0.15s ease-out',
        'dot-bounce': 'dot-bounce 1.3s ease-in-out infinite',
      },
    },
  },
  plugins: [],
}
