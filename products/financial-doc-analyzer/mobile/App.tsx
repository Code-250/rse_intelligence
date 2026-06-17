import React from 'react';
import { QueryClient, QueryClientProvider } from 'react-query';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import HomeScreen from './screens/HomeScreen';
import DocumentPickerScreen from './screens/DocumentPickerScreen';
import UploadProgressScreen from './screens/UploadProgressScreen';
import ResultsScreen from './screens/ResultsScreen';
import DocumentDetailScreen from './screens/DocumentDetailScreen';

const Stack = createNativeStackNavigator();

const queryClient = new QueryClient();

export default function App() {
    return (
        <QueryClientProvider client={queryClient}>
            <NavigationContainer>
                <Stack.Navigator>
                    <Stack.Screen name="Home" component={HomeScreen} />
                    <Stack.Screen name="DocumentPicker" component={DocumentPickerScreen} />
                    <Stack.Screen name="UploadProgress" component={UploadProgressScreen} />
                    <Stack.Screen name="Results" component={ResultsScreen} />
                    <Stack.Screen name="DocumentDetail" component={DocumentDetailScreen} />
                </Stack.Navigator>
            </NavigationContainer>
        </QueryClientProvider>
    );
}
