/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      fontFamily: {
        sans: [
          "Inter",
          "ui-sans-serif",
          "system-ui",
          "-apple-system",
          "Segoe UI",
          "Roboto",
          "Helvetica Neue",
          "Arial",
          "sans-serif",
        ],
      },
      colors: {
        brand: {
          50: "#eef2ff",
          100: "#e0e7ff",
          200: "#c7d2fe",
          300: "#a5b4fc",
          400: "#818cf8",
          500: "#6366f1",
          600: "#635bff",
          700: "#4f46e5",
          800: "#3730a3",
          900: "#312e81",
        },
        ink: {
          50: "#f6f8fb",
          100: "#eef1f6",
          200: "#e3e8ef",
          300: "#cdd5df",
          400: "#9aa4b2",
          500: "#697586",
          600: "#4b5565",
          700: "#364152",
          800: "#202939",
          900: "#121926",
        },
      },
      boxShadow: {
        card: "0 1px 2px rgba(16,24,40,0.06), 0 1px 3px rgba(16,24,40,0.10)",
        elevated:
          "0 4px 6px -2px rgba(16,24,40,0.03), 0 12px 16px -4px rgba(16,24,40,0.08)",
      },
      keyframes: {
        "fade-in": {
          "0%": { opacity: "0", transform: "translateY(4px)" },
          "100%": { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.25s ease-out",
      },
    },
  },
  plugins: [],
};
