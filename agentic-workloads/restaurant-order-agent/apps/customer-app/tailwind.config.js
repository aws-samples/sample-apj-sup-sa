/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{js,jsx}'],
  theme: {
    extend: {
      colors: {
        orange: {
          500: '#FC8019',
          600: '#E67312',
        },
      },
    },
  },
  plugins: [],
}
