import { signIn, signUp } from 'aws-amplify/auth';

// Cognito usernames here are case-sensitive and an Admin adds members by lowercase email.
const normalizeEmail = (value) => value?.trim().toLowerCase();

export const authServices = {
  handleSignIn: (input) => signIn({ ...input, username: normalizeEmail(input.username) }),
  handleSignUp: (input) => {
    const attributes = input.options?.userAttributes;
    return signUp({
      ...input,
      username: normalizeEmail(input.username),
      options: {
        ...input.options,
        userAttributes: { ...attributes, ...(attributes?.email && { email: normalizeEmail(attributes.email) }) },
      },
    });
  },
};

export const AUTH_TEXT_VI = {
  'Sign In': 'Đăng nhập',
  'Sign in': 'Đăng nhập',
  'Signing in': 'Đang đăng nhập',
  'Create Account': 'Tạo tài khoản',
  'Creating Account': 'Đang tạo tài khoản',
  Email: 'Email',
  'Enter your Email': 'Nhập email',
  Password: 'Mật khẩu',
  'Enter your Password': 'Nhập mật khẩu',
  'Confirm Password': 'Nhập lại mật khẩu',
  'Please confirm your Password': 'Nhập lại mật khẩu',
  'Show password': 'Hiện mật khẩu',
  'Hide password': 'Ẩn mật khẩu',
  'Forgot your password?': 'Quên mật khẩu?',
  'Reset your password': 'Đặt lại mật khẩu',
  'Reset your Password': 'Đặt lại mật khẩu',
  'Send code': 'Gửi mã',
  Sending: 'Đang gửi',
  'Back to Sign In': 'Quay lại đăng nhập',
  'We Emailed You': 'Đã gửi mã tới email của bạn',
  'Your code is on the way. To log in, enter the code we emailed to': 'Nhập mã xác nhận đã gửi tới',
  'It may take a minute to arrive': 'Có thể mất khoảng một phút; hãy xem cả mục Spam',
  'Confirm Sign Up': 'Xác nhận đăng ký',
  'Confirmation Code': 'Mã xác nhận',
  'Enter your code': 'Nhập mã',
  'Enter your Confirmation Code': 'Nhập mã xác nhận',
  Confirm: 'Xác nhận',
  Confirming: 'Đang xác nhận',
  'Resend Code': 'Gửi lại mã',
  'New password': 'Mật khẩu mới',
  Submit: 'Gửi',
  Submitting: 'Đang gửi',
  'Your passwords must match': 'Hai mật khẩu phải giống nhau',
  'Incorrect username or password.': 'Sai email hoặc mật khẩu.',
  'User already exists': 'Email này đã có tài khoản.',
  'An account with the given email already exists.': 'Email này đã có tài khoản.',
  'Invalid verification code provided, please try again.': 'Mã xác nhận không đúng, hãy thử lại.',
  'Password did not conform with policy: Password not long enough':
    'Mật khẩu cần ít nhất 12 ký tự, gồm chữ hoa, chữ thường và số.',
  'Password did not conform with policy: Password must have uppercase characters':
    'Mật khẩu cần ít nhất 12 ký tự, gồm chữ hoa, chữ thường và số.',
  'Password did not conform with policy: Password must have lowercase characters':
    'Mật khẩu cần ít nhất 12 ký tự, gồm chữ hoa, chữ thường và số.',
  'Password did not conform with policy: Password must have numeric characters':
    'Mật khẩu cần ít nhất 12 ký tự, gồm chữ hoa, chữ thường và số.',
};
