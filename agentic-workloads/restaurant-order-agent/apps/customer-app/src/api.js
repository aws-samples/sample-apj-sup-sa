/**
 * API client for the Tasty Bites backend.
 */

const API_BASE = import.meta.env.VITE_API_BASE || '/api';

function getHeaders() {
  const headers = { 'Content-Type': 'application/json' };
  const token = localStorage.getItem('auth_token');
  if (token) {
    headers['Authorization'] = `Bearer ${token}`;
  }
  return headers;
}

export async function requestOTP(phone_number) {
  const res = await fetch(`${API_BASE}/auth/request-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone_number }),
  });
  return res.json();
}

export async function verifyOTP(phone_number, otp_code) {
  const res = await fetch(`${API_BASE}/auth/verify-otp`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ phone_number, otp_code }),
  });
  if (!res.ok) throw new Error('Invalid OTP');
  const data = await res.json();
  localStorage.setItem('auth_token', data.access_token);
  localStorage.setItem('phone_number', phone_number);
  return data;
}

export async function getMenu(dietary_flag) {
  const params = dietary_flag ? `?dietary_flag=${dietary_flag}` : '';
  const res = await fetch(`${API_BASE}/menu${params}`, { headers: getHeaders() });
  return res.json();
}

export async function getMenuItem(itemId) {
  const res = await fetch(`${API_BASE}/menu/${itemId}`, { headers: getHeaders() });
  return res.json();
}

export async function getCart() {
  const res = await fetch(`${API_BASE}/cart`, { headers: getHeaders() });
  return res.json();
}

export async function addToCart(menu_item_id, quantity = 1) {
  const res = await fetch(`${API_BASE}/cart/add`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ menu_item_id, quantity }),
  });
  return res.json();
}

export async function placeOrder(payment_status = 'cash-on-delivery') {
  const res = await fetch(`${API_BASE}/orders`, {
    method: 'POST',
    headers: getHeaders(),
    body: JSON.stringify({ payment_status }),
  });
  return res.json();
}

export async function getCurrentOrder() {
  const res = await fetch(`${API_BASE}/orders/current`, { headers: getHeaders() });
  return res.json();
}

export async function getDeliveryStatus(orderId) {
  const res = await fetch(`${API_BASE}/orders/${orderId}/delivery-status`, { headers: getHeaders() });
  return res.json();
}

export async function getProfile() {
  const res = await fetch(`${API_BASE}/profile`, { headers: getHeaders() });
  return res.json();
}

export function isAuthenticated() {
  return !!localStorage.getItem('auth_token');
}

export function logout() {
  localStorage.removeItem('auth_token');
  localStorage.removeItem('phone_number');
}
