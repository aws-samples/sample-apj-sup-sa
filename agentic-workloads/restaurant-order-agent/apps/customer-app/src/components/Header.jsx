import { ShoppingCart, User, LogOut, UtensilsCrossed, UserCircle } from 'lucide-react'

export default function Header({ page, navigate, loggedIn, cartCount, onLoginClick, onLogout }) {
  return (
    <header className="sticky top-0 z-40 bg-white shadow-sm border-b">
      <div className="max-w-7xl mx-auto px-4 h-16 flex items-center justify-between">
        {/* Logo */}
        <div
          className="flex items-center gap-2 cursor-pointer"
          onClick={() => navigate('menu')}
        >
          <UtensilsCrossed className="w-8 h-8 text-orange-500" />
          <span className="text-xl font-bold text-gray-800">Tasty Bites</span>
        </div>

        {/* Nav */}
        <nav className="flex items-center gap-4">
          <button
            onClick={() => navigate('menu')}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
              page === 'menu' ? 'bg-orange-50 text-orange-600' : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Menu
          </button>
          <button
            onClick={() => navigate('orders')}
            className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
              page === 'orders' ? 'bg-orange-50 text-orange-600' : 'text-gray-600 hover:text-gray-900'
            }`}
          >
            Orders
          </button>
          {loggedIn && (
            <button
              onClick={() => navigate('profile')}
              className={`px-3 py-1.5 rounded-lg text-sm font-medium transition ${
                page === 'profile' ? 'bg-orange-50 text-orange-600' : 'text-gray-600 hover:text-gray-900'
              }`}
            >
              Profile
            </button>
          )}

          {/* Cart */}
          <button
            onClick={() => navigate('cart')}
            className="relative p-2 rounded-lg hover:bg-gray-100 transition"
          >
            <ShoppingCart className="w-5 h-5 text-gray-700" />
            {cartCount > 0 && (
              <span className="absolute -top-1 -right-1 w-5 h-5 bg-orange-500 text-white text-xs rounded-full flex items-center justify-center">
                {cartCount}
              </span>
            )}
          </button>

          {/* Auth */}
          {loggedIn ? (
            <button onClick={onLogout} className="p-2 rounded-lg hover:bg-gray-100 transition" title="Logout">
              <LogOut className="w-5 h-5 text-gray-700" />
            </button>
          ) : (
            <button
              onClick={onLoginClick}
              className="flex items-center gap-1 px-3 py-1.5 bg-orange-500 text-white rounded-lg text-sm font-medium hover:bg-orange-600 transition"
            >
              <User className="w-4 h-4" />
              Login
            </button>
          )}
        </nav>
      </div>
    </header>
  )
}
