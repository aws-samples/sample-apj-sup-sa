import { useState } from 'react'
import { X, Phone, Shield } from 'lucide-react'
import { requestOTP, verifyOTP } from '../api'

export default function LoginModal({ onClose, onSuccess }) {
  const [step, setStep] = useState('phone') // 'phone' | 'otp'
  const [phone, setPhone] = useState('+91')
  const [otp, setOtp] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleRequestOTP = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      const data = await requestOTP(phone)
      // If OTP is returned (demo mode), auto-fill it
      if (data.otp) {
        setOtp(data.otp)
      }
      setStep('otp')
    } catch (err) {
      setError('Failed to send OTP. Check phone number.')
    }
    setLoading(false)
  }

  const handleVerifyOTP = async (e) => {
    e.preventDefault()
    setLoading(true)
    setError('')
    try {
      await verifyOTP(phone, otp)
      onSuccess()
    } catch (err) {
      setError('Invalid OTP. Please try again.')
    }
    setLoading(false)
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/50">
      <div className="bg-white rounded-2xl w-full max-w-md mx-4 p-6 relative">
        <button onClick={onClose} className="absolute top-4 right-4 text-gray-400 hover:text-gray-600">
          <X className="w-5 h-5" />
        </button>

        <div className="text-center mb-6">
          <h2 className="text-2xl font-bold text-gray-800">
            {step === 'phone' ? 'Login' : 'Verify OTP'}
          </h2>
          <p className="text-gray-500 text-sm mt-1">
            {step === 'phone'
              ? 'Enter your phone number to receive an OTP'
              : `We sent a 6-digit code to ${phone}`}
          </p>
        </div>

        {error && (
          <div className="bg-red-50 text-red-600 text-sm p-3 rounded-lg mb-4">
            {error}
          </div>
        )}

        {step === 'phone' ? (
          <form onSubmit={handleRequestOTP}>
            <div className="relative">
              <Phone className="absolute left-3 top-3 w-5 h-5 text-gray-400" />
              <input
                type="tel"
                value={phone}
                onChange={(e) => setPhone(e.target.value)}
                placeholder="+14155552671"
                className="w-full pl-10 pr-4 py-3 border border-gray-200 rounded-xl focus:ring-2 focus:ring-orange-500 focus:border-transparent outline-none"
                required
              />
            </div>
            <button
              type="submit"
              disabled={loading || phone.length < 10}
              className="w-full mt-4 py-3 bg-orange-500 text-white font-semibold rounded-xl hover:bg-orange-600 disabled:opacity-50 transition"
            >
              {loading ? 'Sending...' : 'Send OTP'}
            </button>
          </form>
        ) : (
          <form onSubmit={handleVerifyOTP}>
            <div className="relative">
              <Shield className="absolute left-3 top-3 w-5 h-5 text-gray-400" />
              <input
                type="text"
                value={otp}
                onChange={(e) => setOtp(e.target.value)}
                placeholder="Enter 6-digit OTP"
                maxLength={6}
                className="w-full pl-10 pr-4 py-3 border border-gray-200 rounded-xl focus:ring-2 focus:ring-orange-500 focus:border-transparent outline-none text-center text-lg tracking-widest"
                required
              />
            </div>
            <button
              type="submit"
              disabled={loading || otp.length !== 6}
              className="w-full mt-4 py-3 bg-orange-500 text-white font-semibold rounded-xl hover:bg-orange-600 disabled:opacity-50 transition"
            >
              {loading ? 'Verifying...' : 'Verify & Login'}
            </button>
            <button
              type="button"
              onClick={() => { setStep('phone'); setOtp(''); setError('') }}
              className="w-full mt-2 py-2 text-sm text-gray-500 hover:text-gray-700"
            >
              Change phone number
            </button>
          </form>
        )}
      </div>
    </div>
  )
}
