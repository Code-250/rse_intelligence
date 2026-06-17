import axios from 'axios';

const api = axios.create({
    baseURL: process.env.EXPO_PUBLIC_API_URL,
    headers: {
        Authorization: `Bearer ${process.env.EXPO_PUBLIC_JWT_TOKEN}`,
    },
});

export default api;
