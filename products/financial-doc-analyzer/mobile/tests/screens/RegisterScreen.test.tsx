import React from 'react';
import { render } from '@testing-library/react-native';
import RegisterScreen from '../../screens/RegisterScreen';

describe('RegisterScreen', () => {
  it('renders correctly', () => {
    const tree = render(<RegisterScreen />);
    expect(tree).toMatchSnapshot();
  });
});
