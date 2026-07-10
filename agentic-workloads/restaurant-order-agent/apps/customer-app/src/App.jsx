import { useState, useEffect } from 'react'
import { isAuthenticated } from './api'
import Header from './components/Header'
import LoginModal from './components/LoginModal'
import MenuPage from './pages/MenuPage'
import CartPage from './pages/CartPage'
import OrdersPage from './pages/OrdersPage'
import ProfilePage from './pages/ProfilePage'
import VoiceChat from './components/VoiceChat'

export default function App() {
  const [page, setPage] = useState('menu')
  const [showLogin, setShowLogin] = useState(false)
  const [loggedIn, setLoggedIn] = useState(isAuthenticated())
  const [cartCount, setCartCount] = useState(0)

  useEffect(() => {
    setLoggedIn(isAuthenticated())
  }, [showLogin])

  const navigate = (p) => {
    if ((p === 'cart' || p === 'orders' || p === 'profile') && !loggedIn) {
      setShowLogin(true)
      return
    }
    setPage(p)
  }

  return (
    <div className="min-h-screen bg-gray-50">
      <Header
        page={page}
        navigate={navigate}
        loggedIn={loggedIn}
        cartCount={cartCount}
        onLoginClick={() => setShowLogin(true)}
        onLogout={() => { localStorage.clear(); setLoggedIn(false); setPage('menu'); setCartCount(0) }}
      />

      <main className="max-w-7xl mx-auto px-4 pt-4 pb-24">
        {page === 'menu' && (
          <MenuPage
            onAddToCart={() => setCartCount(c => c + 1)}
            loggedIn={loggedIn}
            onNeedLogin={() => setShowLogin(true)}
          />
        )}
        {page === 'cart' && <CartPage navigate={navigate} />}
        {page === 'orders' && <OrdersPage />}
        {page === 'profile' && <ProfilePage />}
      </main>

      <VoiceChat loggedIn={loggedIn} onNeedLogin={() => setShowLogin(true)} />

      {showLogin && (
        <LoginModal
          onClose={() => setShowLogin(false)}
          onSuccess={() => { setLoggedIn(true); setShowLogin(false) }}
        />
      )}
    </div>
  )
}
