import React from 'react';
import { View, Text, Button } from 'react-native';
import { useNavigation } from '@react-navigation/native';

const WelcomeScreen = () => {
  const navigation = useNavigation();

  return (
    <View>
      <Text>App Name</Text>
      <Text>One-line value prop</Text>
      <Button title="Create Account" onPress={() => navigation.navigate('Register')} />
      <Button title="Log In" onPress={() => navigation.navigate('Login')} />
    </View>
  );
};

export default WelcomeScreen;
