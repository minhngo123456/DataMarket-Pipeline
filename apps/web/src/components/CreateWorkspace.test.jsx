import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { hasNoMembership } from '../hooks/useUser.js';
import { marketDataApi } from '../services/marketDataApi.js';
import { CreateWorkspace } from './CreateWorkspace.jsx';

vi.mock('../services/marketDataApi.js', () => ({
  marketDataApi: { createTenant: vi.fn(), getCurrentUser: vi.fn() },
}));

function renderForm() {
  const onChanged = vi.fn();
  const signOut = vi.fn();
  render(<CreateWorkspace email="new@example.com" signOut={signOut} onChanged={onChanged} />);
  return { onChanged, signOut };
}

async function submit(name) {
  fireEvent.change(screen.getByLabelText('Tên workspace'), { target: { value: name } });
  fireEvent.click(screen.getByRole('button', { name: /Tạo workspace/ }));
}

describe('CreateWorkspace', () => {
  beforeEach(() => vi.clearAllMocks());

  it('creates the workspace and reloads the profile', async () => {
    marketDataApi.createTenant.mockResolvedValue({ tenant_id: 't-1' });
    const { onChanged } = renderForm();
    await submit('Shop ABC');
    await waitFor(() => expect(onChanged).toHaveBeenCalled());
    expect(marketDataApi.createTenant).toHaveBeenCalledWith({ name: 'Shop ABC' });
  });

  it('explains a refusal when the account already has a workspace', async () => {
    marketDataApi.createTenant.mockRejectedValue({ response: { status: 409 } });
    const { onChanged } = renderForm();
    await submit('Shop ABC');
    expect(await screen.findByText(/đã thuộc một workspace hoặc đã bị khóa/)).toBeInTheDocument();
    expect(onChanged).not.toHaveBeenCalled();
  });

  it('does not submit a name that is too short', async () => {
    renderForm();
    await submit('x');
    expect(await screen.findByText('Nhập từ 2 đến 80 ký tự.')).toBeInTheDocument();
    expect(marketDataApi.createTenant).not.toHaveBeenCalled();
  });

  it('lets someone waiting for an Admin check again or sign out', () => {
    const { onChanged, signOut } = renderForm();
    fireEvent.click(screen.getByRole('button', { name: 'Kiểm tra lại' }));
    fireEvent.click(screen.getByRole('button', { name: 'Đăng xuất' }));
    expect(onChanged).toHaveBeenCalled();
    expect(signOut).toHaveBeenCalled();
  });
});

describe('hasNoMembership', () => {
  const refusal = (status, message) => ({ response: { status, data: { error: { message } } } });

  it('recognizes only the missing-membership refusal', () => {
    expect(hasNoMembership(refusal(403, 'membership_unavailable'))).toBe(true);
    expect(hasNoMembership(refusal(403, 'role_not_allowed'))).toBe(false);
    expect(hasNoMembership(refusal(500, 'membership_unavailable'))).toBe(false);
    expect(hasNoMembership(new Error('Network Error'))).toBe(false);
  });
});
