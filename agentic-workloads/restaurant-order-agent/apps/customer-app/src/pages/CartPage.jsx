import { useState, useEffect } from 'react'
import { ShoppingBag, Trash2, CreditCard } from 'lucide-react'
import { getCart, placeOrder } from '../api'

export default function CartPage({ navigate }) {
  const [cart, setCart] = useState(null)
  const [loading, setLoading] = useState(true)
  const [placing, setPlacing] = useState(false)
  const [orderPlaced, setOrderPlaced] = useState(null)

  useEffect(() => {
    fetchCart()
  }, [])

  const fetchCart = async () => {
    try {
      const data = await getCart()
      setCart(data)
    } catch (err) {
      console.error('Failed to load cart:', err)
    }
    setLoading(false)
  }

  const handlePlaceOrder = async () => {
    setPlacing(true)
    try {
      const order = await placeOrder('cash-on-delivery')
      setOrderPlaced(order)
    } catch (err) {
      console.error('Order failed:', err)
      alert('Failed to place order. Please try again.')
    }
    setPlacing(false)
  }

  if (loading) {
    return <div className="text-center py-12 text-gray-500">Loading cart...</div>
  }

  if (orderPlaced) {
    return (
      <div className="max-w-md mx-auto text-center py-12">
        <div className="w-20 h-20 bg-green-100 rounded-full flex items-center justify-center mx-auto mb-4">
          <ShoppingBag className="w-10 h-10 text-green-600" />
        </div>
        <h2 className="text-2xl font-bold text-gray-800 mb-2">Order Placed!</h2>
        <p className="text-gray-500 mb-1">Order #{orderPlaced.order_id || orderPlaced.id}</p>
        <p className="text-gray-500 mb-6">Your food is being prepared.</p>
        <button
          onClick={() => navigate('orders')}
          className="px-6 py-3 bg-orange-500 text-white rounded-xl font-medium hover:bg-orange-600 transition"
        >
          Track Order
        </button>
      </div>
    )
  }

  const items = cart?.items || cart?.cart_items || []
  const total = cart?.total || items.reduce((sum, i) => sum + (i.subtotal || i.price * i.quantity), 0)

  if (items.length === 0) {
    return (
      <div className="text-center py-12">
        <ShoppingBag className="w-16 h-16 text-gray-300 mx-auto mb-4" />
        <h2 className="text-xl font-semibold text-gray-600 mb-2">Your cart is empty</h2>
        <p className="text-gray-400 mb-6">Add some delicious items from the menu</p>
        <button
          onClick={() => navigate('menu')}
          className="px-6 py-3 bg-orange-500 text-white rounded-xl font-medium hover:bg-orange-600 transition"
        >
          Browse Menu
        </button>
      </div>
    )
  }

  return (
    <div className="max-w-2xl mx-auto">
      <h1 className="text-2xl font-bold text-gray-800 mb-6">Your Cart</h1>

      <div className="bg-white rounded-xl border divide-y">
        {items.map((item, idx) => (
          <div key={idx} className="flex items-center justify-between p-4">
            <div>
              <h3 className="font-medium text-gray-800">{item.name || `Item #${item.menu_item_id || item.item_id}`}</h3>
              <p className="text-sm text-gray-500">Qty: {item.quantity}</p>
            </div>
            <span className="font-semibold text-gray-800">₹{item.subtotal || item.price * item.quantity}</span>
          </div>
        ))}
      </div>

      {/* Total & Checkout */}
      <div className="bg-white rounded-xl border mt-4 p-4">
        <div className="flex items-center justify-between mb-4">
          <span className="text-gray-600">Total</span>
          <span className="text-xl font-bold text-gray-800">₹{total}</span>
        </div>
        <button
          onClick={handlePlaceOrder}
          disabled={placing}
          className="w-full py-3 bg-orange-500 text-white font-semibold rounded-xl hover:bg-orange-600 disabled:opacity-50 transition flex items-center justify-center gap-2"
        >
          <CreditCard className="w-5 h-5" />
          {placing ? 'Placing Order...' : 'Place Order (Cash on Delivery)'}
        </button>
      </div>
    </div>
  )
}
