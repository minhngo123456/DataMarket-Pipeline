import { signIn, signUp } from 'aws-amplify/auth';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { AUTH_TEXT_VI, authServices } from './authServices.js';

vi.mock('aws-amplify/auth', () => ({ signIn: vi.fn(), signUp: vi.fn() }));

describe('authServices', () => {
  beforeEach(() => vi.clearAllMocks());

  it('signs up with a lowercase email so an Admin can find the account', () => {
    authServices.handleSignUp({
      username: ' New.User@Example.COM ',
      password: 'secret',
      options: { userAttributes: { email: 'New.User@Example.COM' }, autoSignIn: true },
    });
    expect(signUp).toHaveBeenCalledWith({
      username: 'new.user@example.com',
      password: 'secret',
      options: { userAttributes: { email: 'new.user@example.com' }, autoSignIn: true },
    });
  });

  it('signs in with the same normalized email', () => {
    authServices.handleSignIn({ username: 'New.User@Example.COM', password: 'secret' });
    expect(signIn).toHaveBeenCalledWith({ username: 'new.user@example.com', password: 'secret' });
  });

  it('labels the sign-up tab in Vietnamese', () => {
    expect(AUTH_TEXT_VI['Create Account']).toBe('Tạo tài khoản');
  });
});
