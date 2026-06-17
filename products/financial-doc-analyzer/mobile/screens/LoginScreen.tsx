import React, { useState } from 'react';
import { View, Text, TextInput, Button } from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { z } from 'zod';
import { useForm } from 'react-hook-form';
import { zodResolver } from '@hookform/resolvers/zod';
import { useMutation } from 'react-query';
import { axios } from '../../lib/axios';

const loginSchema = z.object({
  email: z.string().email(),
  password: z.string().min(8),
});

const LoginScreen = () => {
  const navigation = useNavigation();
  const { register, handleSubmit, formState: { errors } } = useForm({
    resolver: zodResolver(loginSchema),
  });
  const { mutate, isLoading } = useMutation(
    async (data: any) => {
      const response = await axios.post('/api/v1/auth/login', data);
      return response.data;
    },
    {
      onSuccess: (data) => {
        navigation.navigate('Home');
      },
    }
  );

  const onSubmit = async (data: any) => {
    mutate(data);
  };

  return (
    <View>
      <Text>Log In</Text>
      <TextInput {...register('email')} placeholder="Email" />
      <Text style={{ color: 'red' }}>{errors.email?.message}</Text>
      <TextInput {...register('password')} placeholder="Password" secureTextEntry />
      <Text style={{ color: 'red' }}>{errors.password?.message}</Text>
      <Button title="Login" onPress={handleSubmit(onSubmit)} disabled={isLoading} />
    </View>
  );
};

export default LoginScreen;
